from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class InputBucket:
    bucket_start: datetime = field(default_factory=_now)
    bucket_end: datetime | None = None

    key_press_count: int = 0
    mouse_distance_px: float = 0.0
    mouse_click_left: int = 0
    mouse_click_right: int = 0
    mouse_click_middle: int = 0
    scroll_distance: int = 0

    @property
    def key_rate_per_sec(self) -> float | None:
        if self.bucket_end is None:
            return None
        dur = (self.bucket_end - self.bucket_start).total_seconds()
        if dur <= 0:
            return 0.0
        return self.key_press_count / dur

    @property
    def has_any_input(self) -> bool:
        return (
            self.key_press_count > 0
            or self.mouse_distance_px > 0
            or self.mouse_click_left > 0
            or self.scroll_distance > 0
        )


@dataclass
class WindowInfo:
    wm_class: str = ""
    title: str = ""
    pid: int = 0


@dataclass
class WindowSession:
    wm_class: str
    window_title: str
    pid: int
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None


@dataclass
class MicState:
    is_active: bool = False
    source_node: str = ""
    client_app: str = ""
    client_pid: int = 0


@dataclass
class ActivityScore:
    window_start: datetime
    window_end: datetime
    wm_class: str
    score: float
    score_label: str
    key_presses: int = 0
    mouse_distance: float = 0.0
    clicks: int = 0
    scroll: int = 0
    mic_active: bool = False


@dataclass
class IdleEvent:
    timestamp: datetime
    idle_ms: int
    is_locked: bool = False
