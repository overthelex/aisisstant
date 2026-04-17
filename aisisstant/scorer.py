from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import TYPE_CHECKING

from .models import ActivityScore, IdleEvent, InputBucket, _now

if TYPE_CHECKING:
    from .db import BatchWriter

log = logging.getLogger(__name__)


def compute_score(
    key_presses: int,
    mouse_distance: float,
    clicks: int,
    scroll: int,
    mic_active: bool,
    idle_ms: int,
) -> tuple[float, str]:
    """Compute activity score from input metrics.

    Returns (score 0.0-1.0, label).
    """
    k_score = min(key_presses / 60.0, 1.0)
    d_score = min(mouse_distance / 3000.0, 1.0)
    c_score = min(clicks / 10.0, 1.0)
    s_score = min(scroll / 20.0, 1.0)

    input_score = (
        0.40 * k_score
        + 0.25 * d_score
        + 0.20 * c_score
        + 0.10 * s_score
        + 0.05 * (1.0 if mic_active else 0.0)
    )

    if idle_ms > 25000:
        idle_factor = 0.0
    elif idle_ms > 15000:
        idle_factor = 0.3
    elif idle_ms > 5000:
        idle_factor = 0.7
    else:
        idle_factor = 1.0

    score = input_score * idle_factor

    if score >= 0.3:
        label = "active"
    elif score >= 0.05:
        label = "passive"
    else:
        label = "idle"

    return score, label


async def get_idle_ms() -> int:
    """Get system idle time via Mutter IdleMonitor (GNOME/Wayland)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Mutter.IdleMonitor",
            "--object-path",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "--method",
            "org.gnome.Mutter.IdleMonitor.GetIdletime",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            # Output: (uint64 12345,)
            raw = stdout.decode().strip()
            # Extract number
            for part in raw.split():
                cleaned = part.rstrip(",).").lstrip("(")
                if cleaned.isdigit():
                    return int(cleaned)
    except Exception:
        pass
    return 0


class ActivityScorer:
    """Computes activity scores every score_window seconds."""

    def __init__(
        self,
        writer: BatchWriter,
        score_window: int = 30,
    ):
        self.writer = writer
        self.score_window = score_window
        self._input_buckets: list[InputBucket] = []
        self._current_wm_class: str = ""
        self._current_window_title: str = ""
        self._current_cwd: str = ""
        self._mic_active: bool = False

    def feed_input(self, bucket: InputBucket) -> None:
        self._input_buckets.append(bucket)

    def set_window(
        self, wm_class: str, window_title: str = "", cwd: str = ""
    ) -> None:
        self._current_wm_class = wm_class
        self._current_window_title = window_title
        self._current_cwd = cwd

    def set_mic(self, active: bool) -> None:
        self._mic_active = active

    async def run(self) -> None:
        log.info("ActivityScorer started (window=%ds)", self.score_window)
        while True:
            await asyncio.sleep(self.score_window)
            await self._compute()

    async def _compute(self) -> None:
        now = _now()
        window_start = now.__class__(
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second - (now.second % self.score_window),
            tzinfo=now.tzinfo,
        )

        # Aggregate input buckets
        buckets = self._input_buckets
        self._input_buckets = []

        total_keys = sum(b.key_press_count for b in buckets)
        total_distance = sum(b.mouse_distance_px for b in buckets)
        total_clicks = sum(
            b.mouse_click_left + b.mouse_click_right + b.mouse_click_middle
            for b in buckets
        )
        total_scroll = sum(b.scroll_distance for b in buckets)

        idle_ms = await get_idle_ms()

        # Log idle event
        idle_event = IdleEvent(timestamp=now, idle_ms=idle_ms)
        await self.writer.put("idle_events", idle_event)

        score, label = compute_score(
            total_keys, total_distance, total_clicks, total_scroll,
            self._mic_active, idle_ms,
        )

        wm_class = self._current_wm_class or "unknown"

        activity = ActivityScore(
            window_start=window_start,
            window_end=now,
            wm_class=wm_class,
            window_title=self._current_window_title,
            score=score,
            score_label=label,
            key_presses=total_keys,
            mouse_distance=total_distance,
            clicks=total_clicks,
            scroll=total_scroll,
            mic_active=self._mic_active,
            cwd=self._current_cwd,
        )

        await self.writer.put("activity_scores", activity)
        log.debug(
            "Score: %.2f (%s) wm=%s keys=%d dist=%.0f clicks=%d idle=%dms",
            score,
            label,
            wm_class,
            total_keys,
            total_distance,
            total_clicks,
            idle_ms,
        )
