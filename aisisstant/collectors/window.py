from __future__ import annotations

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING

from ..models import WindowInfo, WindowSession, _now
from .base import BaseCollector

if TYPE_CHECKING:
    from ..db import BatchWriter


class WindowCollector(BaseCollector):
    name = "window"

    def __init__(self, writer: BatchWriter, poll_seconds: float = 2.0):
        super().__init__(writer)
        self.poll_seconds = poll_seconds
        self._current_session: WindowSession | None = None

    async def run(self) -> None:
        self.log.info("Window collector started (poll every %.1fs)", self.poll_seconds)
        while True:
            try:
                info = await self._get_active_window()
                await self._handle_window(info)
            except Exception:
                self.log.exception("Error polling window")
            await asyncio.sleep(self.poll_seconds)

    async def _get_active_window(self) -> WindowInfo:
        """Try aisisstant extension, then switchamba, then xdotool."""
        info = await self._try_dbus()
        if info is not None:
            return info
        info = await self._try_switchamba()
        if info is not None:
            return info
        return await self._try_xdotool()

    async def _try_switchamba(self) -> WindowInfo | None:
        """Fallback: use existing switchamba extension (wm_class only)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell",
                "--object-path",
                "/com/switchamba/WindowInfo",
                "--method",
                "com.switchamba.WindowInfo.GetFocusedApp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            if proc.returncode != 0:
                return None
            raw = stdout.decode().strip()
            # Output: ('gnome-terminal-server',)
            start = raw.find("'") + 1
            end = raw.rfind("'")
            if start <= 0 or end <= start:
                return None
            wm_class = raw[start:end]
            if wm_class:
                return WindowInfo(wm_class=wm_class)
            return None
        except Exception:
            return None

    async def _try_dbus(self) -> WindowInfo | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell",
                "--object-path",
                "/com/aisisstant/WindowTracker",
                "--method",
                "com.aisisstant.WindowTracker.GetActiveWindow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            if proc.returncode != 0:
                return None
            # gdbus output looks like: ('{"wm_class":"...","title":"...","pid":123}',)
            raw = stdout.decode().strip()
            # Extract JSON string between first pair of quotes
            start = raw.find("'") + 1
            end = raw.rfind("'")
            if start <= 0 or end <= start:
                return None
            json_str = raw[start:end]
            # Unescape gdbus quoting
            json_str = json_str.replace("\\'", "'")
            data = json.loads(json_str)
            return WindowInfo(
                wm_class=data.get("wm_class", ""),
                title=data.get("title", ""),
                pid=data.get("pid", 0),
            )
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            return None

    async def _try_xdotool(self) -> WindowInfo:
        """Fallback: use xdotool (works for Xwayland windows)."""
        info = WindowInfo()
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "getactivewindow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            if proc.returncode != 0:
                return info
            wid = stdout.decode().strip()
            if not wid:
                return info

            # Get wm_class
            proc2 = await asyncio.create_subprocess_exec(
                "xdotool",
                "getactivewindow",
                "getwindowclassname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=3)
            if proc2.returncode == 0:
                info.wm_class = stdout2.decode().strip()

            # Get title
            proc3 = await asyncio.create_subprocess_exec(
                "xdotool",
                "getactivewindow",
                "getwindowname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout3, _ = await asyncio.wait_for(proc3.communicate(), timeout=3)
            if proc3.returncode == 0:
                info.title = stdout3.decode().strip()

            # Get PID
            proc4 = await asyncio.create_subprocess_exec(
                "xdotool",
                "getactivewindow",
                "getwindowpid",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout4, _ = await asyncio.wait_for(proc4.communicate(), timeout=3)
            if proc4.returncode == 0:
                pid_str = stdout4.decode().strip()
                if pid_str.isdigit():
                    info.pid = int(pid_str)
        except (asyncio.TimeoutError, Exception):
            pass
        return info

    async def _handle_window(self, info: WindowInfo) -> None:
        if not info.wm_class:
            return

        if self._current_session is None or self._current_session.wm_class != info.wm_class or self._current_session.window_title != info.title:
            # Close previous session
            if self._current_session is not None:
                now = _now()
                self._current_session.ended_at = now
                await self.writer.put(
                    "window_session_close",
                    {
                        "ended_at": now,
                        "wm_class": self._current_session.wm_class,
                        "started_at": self._current_session.started_at,
                    },
                )

            # Open new session
            self._current_session = WindowSession(
                wm_class=info.wm_class,
                window_title=info.title,
                pid=info.pid,
            )
            await self.writer.put("window_sessions", self._current_session)
