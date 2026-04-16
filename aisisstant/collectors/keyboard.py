from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import TYPE_CHECKING

import evdev
from evdev import ecodes

from ..models import InputBucket, _now
from .base import BaseCollector

if TYPE_CHECKING:
    from ..db import BatchWriter


def find_keyboards() -> list[str]:
    """Auto-detect keyboard devices from /dev/input/event*."""
    keyboards: list[str] = []
    for path in sorted(Path("/dev/input").glob("event*")):
        try:
            dev = evdev.InputDevice(str(path))
            caps = dev.capabilities(verbose=False)
            if ecodes.EV_KEY not in caps:
                dev.close()
                continue
            keys = set(caps[ecodes.EV_KEY])
            # Must have standard letter keys (KEY_A=30 .. KEY_Z=44 range)
            has_letters = any(30 <= k <= 44 for k in keys)
            if has_letters:
                keyboards.append(str(path))
            dev.close()
        except (PermissionError, OSError):
            continue
    return keyboards


class KeyboardCollector(BaseCollector):
    name = "keyboard"

    def __init__(self, writer: BatchWriter, bucket_seconds: int = 5):
        super().__init__(writer)
        self.bucket_seconds = bucket_seconds
        self._bucket = InputBucket()

    async def run(self) -> None:
        while True:
            devices = find_keyboards()
            if not devices:
                self.log.warning("No keyboard devices found, retrying in 60s")
                await asyncio.sleep(60)
                continue

            self.log.info("Monitoring keyboards: %s", devices)
            tasks = [
                asyncio.create_task(self._read_device(path)) for path in devices
            ]
            flush_task = asyncio.create_task(self._flush_loop())

            done, pending = await asyncio.wait(
                [*tasks, flush_task], return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            # Log any exception, then retry device scan
            for t in done:
                if t.exception():
                    self.log.warning("Keyboard task ended: %s", t.exception())
            await asyncio.sleep(5)

    async def _read_device(self, path: str) -> None:
        dev = evdev.InputDevice(path)
        self.log.info("Opened keyboard: %s (%s)", dev.name, path)
        try:
            async for event in dev.async_read_loop():
                if event.type == ecodes.EV_KEY and event.value == 1:  # key down
                    self._bucket.key_press_count += 1
        except OSError as e:
            self.log.warning("Keyboard device lost %s: %s", path, e)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.bucket_seconds)
            bucket = self._bucket
            bucket.bucket_end = _now()
            self._bucket = InputBucket()
            if bucket.key_press_count > 0:
                await self.writer.put("input_activity", bucket)
