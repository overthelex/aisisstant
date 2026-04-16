from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aisisstant.models import (
    ActivityScore,
    IdleEvent,
    InputBucket,
    MicState,
    WindowInfo,
    WindowSession,
    _now,
)


class TestNow:
    def test_returns_utc_datetime(self):
        result = _now()
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc

    def test_is_recent(self):
        before = datetime.now(timezone.utc)
        result = _now()
        after = datetime.now(timezone.utc)
        assert before <= result <= after


class TestInputBucket:
    def test_defaults(self):
        b = InputBucket()
        assert b.key_press_count == 0
        assert b.mouse_distance_px == 0.0
        assert b.mouse_click_left == 0
        assert b.mouse_click_right == 0
        assert b.mouse_click_middle == 0
        assert b.scroll_distance == 0
        assert b.bucket_end is None

    def test_key_rate_per_sec_no_end(self):
        b = InputBucket()
        assert b.key_rate_per_sec is None

    def test_key_rate_per_sec_zero_duration(self):
        now = _now()
        b = InputBucket(bucket_start=now, bucket_end=now, key_press_count=10)
        assert b.key_rate_per_sec == 0.0

    def test_key_rate_per_sec_normal(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=5)
        b = InputBucket(bucket_start=start, bucket_end=end, key_press_count=10)
        assert b.key_rate_per_sec == 2.0

    def test_key_rate_per_sec_fractional(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=3)
        b = InputBucket(bucket_start=start, bucket_end=end, key_press_count=1)
        assert abs(b.key_rate_per_sec - 1 / 3) < 1e-6

    def test_has_any_input_empty(self):
        b = InputBucket()
        assert b.has_any_input is False

    def test_has_any_input_keys_only(self):
        b = InputBucket(key_press_count=1)
        assert b.has_any_input is True

    def test_has_any_input_mouse_distance_only(self):
        b = InputBucket(mouse_distance_px=0.1)
        assert b.has_any_input is True

    def test_has_any_input_left_click_only(self):
        b = InputBucket(mouse_click_left=1)
        assert b.has_any_input is True

    def test_has_any_input_scroll_only(self):
        b = InputBucket(scroll_distance=1)
        assert b.has_any_input is True

    def test_has_any_input_right_click_not_counted(self):
        """right_click and middle_click alone do not trigger has_any_input."""
        b = InputBucket(mouse_click_right=5, mouse_click_middle=3)
        assert b.has_any_input is False


class TestWindowInfo:
    def test_defaults(self):
        w = WindowInfo()
        assert w.wm_class == ""
        assert w.title == ""
        assert w.pid == 0

    def test_with_values(self):
        w = WindowInfo(wm_class="firefox", title="GitHub", pid=1234)
        assert w.wm_class == "firefox"
        assert w.title == "GitHub"
        assert w.pid == 1234


class TestWindowSession:
    def test_creation(self):
        s = WindowSession(wm_class="code", window_title="main.py", pid=999)
        assert s.wm_class == "code"
        assert s.window_title == "main.py"
        assert s.pid == 999
        assert s.ended_at is None
        assert s.started_at.tzinfo == timezone.utc

    def test_ended_at_settable(self):
        s = WindowSession(wm_class="x", window_title="y", pid=1)
        now = _now()
        s.ended_at = now
        assert s.ended_at == now


class TestMicState:
    def test_defaults(self):
        m = MicState()
        assert m.is_active is False
        assert m.source_node == ""
        assert m.client_app == ""
        assert m.client_pid == 0

    def test_active(self):
        m = MicState(
            is_active=True,
            source_node="alsa_input.usb",
            client_app="zoom",
            client_pid=5555,
        )
        assert m.is_active is True
        assert m.client_app == "zoom"


class TestActivityScore:
    def test_creation(self):
        now = _now()
        a = ActivityScore(
            window_start=now,
            window_end=now,
            wm_class="firefox",
            score=0.75,
            score_label="active",
            key_presses=100,
            mouse_distance=2000.0,
            clicks=15,
            scroll=5,
            mic_active=True,
        )
        assert a.score == 0.75
        assert a.score_label == "active"
        assert a.mic_active is True

    def test_defaults(self):
        now = _now()
        a = ActivityScore(
            window_start=now, window_end=now, wm_class="x", score=0.0, score_label="idle"
        )
        assert a.key_presses == 0
        assert a.mouse_distance == 0.0
        assert a.clicks == 0
        assert a.scroll == 0
        assert a.mic_active is False


class TestIdleEvent:
    def test_creation(self):
        now = _now()
        e = IdleEvent(timestamp=now, idle_ms=5000)
        assert e.idle_ms == 5000
        assert e.is_locked is False

    def test_locked(self):
        now = _now()
        e = IdleEvent(timestamp=now, idle_ms=0, is_locked=True)
        assert e.is_locked is True
