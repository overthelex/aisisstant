from __future__ import annotations

import asyncio
import logging
import signal

from .config import Config
from .db import BatchWriter, create_pool, run_migrations
from .collectors.keyboard import KeyboardCollector, find_keyboards
from .collectors.mouse import MouseCollector, find_mice
from .collectors.window import WindowCollector
from .collectors.microphone import MicrophoneCollector
from .models import InputBucket, _now
from .scorer import ActivityScorer

log = logging.getLogger("aisisstant")


class Orchestrator:
    """Wires collectors -> scorer -> batch writer and runs them all."""

    def __init__(self, config: Config):
        self.config = config

    async def run(self) -> None:
        pool = await create_pool(self.config)
        await run_migrations(pool)
        log.info("Database ready")

        writer = BatchWriter(pool, flush_interval=self.config.input_bucket_seconds)
        scorer = ActivityScorer(
            writer, score_window=self.config.score_window_seconds
        )

        kbd = KeyboardCollector(writer, bucket_seconds=self.config.input_bucket_seconds)
        mouse = MouseCollector(writer, bucket_seconds=self.config.input_bucket_seconds)
        window = WindowCollector(writer, poll_seconds=self.config.window_poll_seconds)
        mic = MicrophoneCollector(writer, poll_seconds=self.config.mic_poll_seconds)

        tasks = [
            asyncio.create_task(writer.run(), name="writer"),
            asyncio.create_task(
                self._run_keyboard(kbd, scorer), name="keyboard"
            ),
            asyncio.create_task(
                self._run_mouse(mouse, scorer), name="mouse"
            ),
            asyncio.create_task(
                self._run_window(window, scorer), name="window"
            ),
            asyncio.create_task(
                self._run_mic(mic, scorer), name="mic"
            ),
            asyncio.create_task(scorer.run(), name="scorer"),
        ]

        log.info("All collectors started")

        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in done:
            if t.exception():
                log.error("Task %s failed: %s", t.get_name(), t.exception())
        for t in pending:
            t.cancel()
        await pool.close()

    async def _run_keyboard(
        self, kbd: KeyboardCollector, scorer: ActivityScorer
    ) -> None:
        while True:
            devices = find_keyboards()
            if not devices:
                kbd.log.warning("No keyboard devices found, retrying in 60s")
                await asyncio.sleep(60)
                continue

            kbd.log.info("Monitoring keyboards: %s", devices)
            read_tasks = [
                asyncio.create_task(kbd._read_device(path)) for path in devices
            ]

            async def kbd_flush():
                while True:
                    await asyncio.sleep(kbd.bucket_seconds)
                    bucket = kbd._bucket
                    bucket.bucket_end = _now()
                    kbd._bucket = InputBucket()
                    if bucket.key_press_count > 0:
                        scorer.feed_input(bucket)
                        await kbd.writer.put("input_activity", bucket)

            flush_task = asyncio.create_task(kbd_flush())
            done, pending = await asyncio.wait(
                [*read_tasks, flush_task], return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            for t in done:
                if t.exception():
                    kbd.log.warning("Keyboard task ended: %s", t.exception())
            await asyncio.sleep(5)

    async def _run_mouse(
        self, mouse: MouseCollector, scorer: ActivityScorer
    ) -> None:
        while True:
            devices = find_mice()
            if not devices:
                mouse.log.warning("No mouse devices found, retrying in 60s")
                await asyncio.sleep(60)
                continue

            mouse.log.info("Monitoring mice: %s", devices)
            read_tasks = [
                asyncio.create_task(mouse._read_device(path)) for path in devices
            ]

            async def mouse_flush():
                while True:
                    await asyncio.sleep(mouse.bucket_seconds)
                    bucket = mouse._bucket
                    bucket.bucket_end = _now()
                    mouse._bucket = InputBucket()
                    if bucket.has_any_input:
                        scorer.feed_input(bucket)
                        await mouse.writer.put("input_activity", bucket)

            flush_task = asyncio.create_task(mouse_flush())
            done, pending = await asyncio.wait(
                [*read_tasks, flush_task], return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            for t in done:
                if t.exception():
                    mouse.log.warning("Mouse task ended: %s", t.exception())
            await asyncio.sleep(5)

    async def _run_window(
        self, window: WindowCollector, scorer: ActivityScorer
    ) -> None:
        window.log.info(
            "Window collector started (poll every %.1fs)", window.poll_seconds
        )
        while True:
            try:
                info = await window._get_active_window()
                await window._handle_window(info)
                if info.wm_class:
                    scorer.set_window(info.wm_class, info.title)
            except Exception:
                window.log.exception("Error polling window")
            await asyncio.sleep(window.poll_seconds)

    async def _run_mic(
        self, mic: MicrophoneCollector, scorer: ActivityScorer
    ) -> None:
        mic.log.info(
            "Microphone collector started (poll every %.1fs)", mic.poll_seconds
        )
        while True:
            try:
                state = await mic._poll_pipewire()
                scorer.set_mic(state.is_active)
                if state.is_active or state.is_active != mic._last_active:
                    await mic.writer.put("mic_activity", state)
                    if state.is_active != mic._last_active:
                        mic.log.info(
                            "Mic %s (app=%s)",
                            "ACTIVE" if state.is_active else "INACTIVE",
                            state.client_app,
                        )
                mic._last_active = state.is_active
            except Exception:
                mic.log.exception("Error polling microphone")
            await asyncio.sleep(mic.poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = Config()
    orchestrator = Orchestrator(config)

    async def run_with_shutdown():
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def handle_signal():
            log.info("Shutdown signal received")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

        # Run orchestrator, exiting non-zero if it crashes so systemd
        # can restart us.
        task = asyncio.create_task(orchestrator.run())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            [task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if task in done:
            exc = task.exception()
            if exc is not None:
                log.error("Orchestrator crashed", exc_info=exc)
                raise SystemExit(1)
        else:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log.info("Aisisstant stopped")

    try:
        asyncio.run(run_with_shutdown())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
