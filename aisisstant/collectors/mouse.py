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


def find_mice() -> list[str]:
    """Auto-detect mouse/trackpad devices from /dev/input/event*."""
    mice: list[str] = []
    for path in sorted(Path("/dev/input").glob("event*")):
        try:
            dev = evdev.InputDevice(str(path))
            caps = dev.capabilities(verbose=False)
            if ecodes.EV_REL not in caps:
                dev.close()
                continue
            rel_axes = set(caps[ecodes.EV_REL])
            # Must have REL_X and REL_Y
            if ecodes.REL_X in rel_axes and ecodes.REL_Y in rel_axes:
                mice.append(str(path))
            dev.close()
        except (PermissionError, OSError):
            continue
    return mice


class MouseCollector(BaseCollector):
    name = "mouse"

    def __init__(self, writer: BatchWriter, bucket_seconds: int = 5):
        super().__init__(writer)
        self.bucket_seconds = bucket_seconds
        self._bucket = InputBucket()
        self._dx = 0.0
        self._dy = 0.0

    async def run(self) -> None:
        while True:
            devices = find_mice()
            if not devices:
                self.log.warning("No mouse devices found, retrying in 60s")
                await asyncio.sleep(60)
                continue

            self.log.info("Monitoring mice: %s", devices)
            tasks = [
                asyncio.create_task(self._read_device(path)) for path in devices
            ]
            flush_task = asyncio.create_task(self._flush_loop())

            done, pending = await asyncio.wait(
                [*tasks, flush_task], return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            for t in done:
                if t.exception():
                    self.log.warning("Mouse task ended: %s", t.exception())
            await asyncio.sleep(5)

    async def _read_device(self, path: str) -> None:
        dev = evdev.InputDevice(path)
        self.log.info("Opened mouse: %s (%s)", dev.name, path)
        try:
            async for event in dev.async_read_loop():
                if event.type == ecodes.EV_REL:
                    if event.code == ecodes.REL_X:
                        self._dx += event.value
                    elif event.code == ecodes.REL_Y:
                        self._dy += event.value
                    elif event.code == ecodes.REL_WHEEL:
                        self._bucket.scroll_distance += abs(event.value)
                elif event.type == ecodes.EV_KEY:
                    if event.value == 1:  # press
                        if event.code == ecodes.BTN_LEFT:
                            self._bucket.mouse_click_left += 1
                        elif event.code == ecodes.BTN_RIGHT:
                            self._bucket.mouse_click_right += 1
                        elif event.code == ecodes.BTN_MIDDLE:
                            self._bucket.mouse_click_middle += 1

                # Accumulate distance on each SYN_REPORT
                if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    if self._dx != 0 or self._dy != 0:
                        self._bucket.mouse_distance_px += math.sqrt(
                            self._dx ** 2 + self._dy ** 2
                        )
                        self._dx = 0.0
                        self._dy = 0.0
        except OSError as e:
            self.log.warning("Mouse device lost %s: %s", path, e)
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
            if bucket.has_any_input:
                await self.writer.put("input_activity", bucket)
