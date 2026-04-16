from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aisisstant.models import InputBucket, _now
from aisisstant.scorer import ActivityScorer, compute_score, get_idle_ms


class TestComputeScore:
    """Tests for the pure compute_score function."""

    # --- Label thresholds ---

    def test_zero_input_zero_idle_is_idle(self):
        score, label = compute_score(0, 0.0, 0, 0, False, 0)
        assert score == 0.0
        assert label == "idle"

    def test_high_activity_is_active(self):
        score, label = compute_score(100, 5000.0, 20, 30, True, 0)
        assert score == 1.0
        assert label == "active"

    def test_moderate_typing_is_passive(self):
        # 30 keys in 30s: 0.40 * (30/60) = 0.20, which is passive
        score, label = compute_score(30, 0.0, 0, 0, False, 0)
        assert label == "passive"
        assert abs(score - 0.20) < 1e-6

    def test_light_activity_is_idle(self):
        # 3 keys + 100px: 0.40*(3/60) + 0.25*(100/3000) = 0.02 + 0.0083 = 0.028
        score, label = compute_score(3, 100.0, 0, 0, False, 0)
        assert label == "idle"
        assert score < 0.05

    def test_passive_zone(self):
        # Enough activity to be passive but not active
        # 10 keys + 500px mouse: 0.40*(10/60) + 0.25*(500/3000) = 0.067 + 0.042 = 0.109
        score, label = compute_score(10, 500.0, 0, 0, False, 0)
        assert label == "passive"
        assert 0.05 <= score < 0.3

    def test_boundary_active_passive(self):
        # score == 0.3 should be "active"
        # 0.40 * (k/60) = 0.3 => k = 45
        score, label = compute_score(45, 0.0, 0, 0, False, 0)
        assert label == "active"
        assert abs(score - 0.3) < 1e-6

    def test_boundary_passive_idle(self):
        # score < 0.05 is idle
        # 0.40 * (1/60) = 0.0067
        score, label = compute_score(1, 0.0, 0, 0, False, 0)
        assert label == "idle"
        assert score < 0.05

    # --- Input components ---

    def test_keyboard_weight(self):
        score, _ = compute_score(60, 0.0, 0, 0, False, 0)
        assert abs(score - 0.40) < 1e-6

    def test_mouse_distance_weight(self):
        score, _ = compute_score(0, 3000.0, 0, 0, False, 0)
        assert abs(score - 0.25) < 1e-6

    def test_clicks_weight(self):
        score, _ = compute_score(0, 0.0, 10, 0, False, 0)
        assert abs(score - 0.20) < 1e-6

    def test_scroll_weight(self):
        score, _ = compute_score(0, 0.0, 0, 20, False, 0)
        assert abs(score - 0.10) < 1e-6

    def test_mic_weight(self):
        score, _ = compute_score(0, 0.0, 0, 0, True, 0)
        assert abs(score - 0.05) < 1e-6

    def test_all_components_saturated(self):
        score, _ = compute_score(60, 3000.0, 10, 20, True, 0)
        assert abs(score - 1.0) < 1e-6

    def test_components_capped_at_max(self):
        # Values above threshold should cap at 1.0 per component
        score, _ = compute_score(200, 10000.0, 50, 100, True, 0)
        assert abs(score - 1.0) < 1e-6

    # --- Idle factor ---

    def test_idle_under_5s_full_factor(self):
        score_active, _ = compute_score(60, 0.0, 0, 0, False, 0)
        score_idle, _ = compute_score(60, 0.0, 0, 0, False, 4999)
        assert score_active == score_idle

    def test_idle_5s_to_15s_factor_07(self):
        score, _ = compute_score(60, 0.0, 0, 0, False, 10000)
        assert abs(score - 0.40 * 0.7) < 1e-6

    def test_idle_15s_to_25s_factor_03(self):
        score, _ = compute_score(60, 0.0, 0, 0, False, 20000)
        assert abs(score - 0.40 * 0.3) < 1e-6

    def test_idle_over_25s_factor_0(self):
        score, label = compute_score(60, 3000.0, 10, 20, True, 26000)
        assert score == 0.0
        assert label == "idle"

    def test_idle_exactly_5000(self):
        """5000ms is NOT > 5000, so full factor."""
        score, _ = compute_score(60, 0.0, 0, 0, False, 5000)
        assert abs(score - 0.40) < 1e-6

    def test_idle_exactly_5001(self):
        """5001ms IS > 5000, so 0.7 factor."""
        score, _ = compute_score(60, 0.0, 0, 0, False, 5001)
        assert abs(score - 0.40 * 0.7) < 1e-6

    def test_idle_exactly_15001(self):
        score, _ = compute_score(60, 0.0, 0, 0, False, 15001)
        assert abs(score - 0.40 * 0.3) < 1e-6

    def test_idle_exactly_25001(self):
        score, _ = compute_score(60, 0.0, 0, 0, False, 25001)
        assert score == 0.0

    # --- Edge cases ---

    def test_negative_values_dont_crash(self):
        # Shouldn't happen in practice but ensure no exceptions
        score, label = compute_score(-1, -100.0, -5, -10, False, -1000)
        assert isinstance(score, float)
        assert label in ("active", "passive", "idle")


class TestGetIdleMs:
    @pytest.mark.asyncio
    async def test_parses_mutter_output(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"(uint64 12345,)\n", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.scorer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await get_idle_ms()
        assert result == 12345

    @pytest.mark.asyncio
    async def test_parses_large_value(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"(uint64 999999,)\n", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.scorer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await get_idle_ms()
        assert result == 999999

    @pytest.mark.asyncio
    async def test_returns_0_on_failure(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("aisisstant.scorer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await get_idle_ms()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_0_on_timeout(self):
        with patch(
            "aisisstant.scorer.asyncio.create_subprocess_exec",
            side_effect=asyncio.TimeoutError,
        ):
            result = await get_idle_ms()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_0_on_garbage_output(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"garbage data here\n", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.scorer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await get_idle_ms()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_0_on_empty_output(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.scorer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await get_idle_ms()
        assert result == 0


class TestActivityScorer:
    def test_feed_input_accumulates(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        b1 = InputBucket(key_press_count=10)
        b2 = InputBucket(key_press_count=20)
        scorer.feed_input(b1)
        scorer.feed_input(b2)
        assert len(scorer._input_buckets) == 2

    def test_set_window(self, mock_writer):
        scorer = ActivityScorer(mock_writer)
        scorer.set_window("firefox")
        assert scorer._current_wm_class == "firefox"

    def test_set_mic(self, mock_writer):
        scorer = ActivityScorer(mock_writer)
        scorer.set_mic(True)
        assert scorer._mic_active is True
        scorer.set_mic(False)
        assert scorer._mic_active is False

    @pytest.mark.asyncio
    async def test_compute_writes_to_writer(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        scorer.set_window("code")

        b = InputBucket(key_press_count=50, mouse_distance_px=1000.0)
        scorer.feed_input(b)

        with patch("aisisstant.scorer.get_idle_ms", return_value=0):
            await scorer._compute()

        # Should have written idle_events and activity_scores
        assert mock_writer.put.call_count == 2
        calls = [c[0] for c in mock_writer.put.call_args_list]
        tables = [c[0] for c in calls]
        assert "idle_events" in tables
        assert "activity_scores" in tables

    @pytest.mark.asyncio
    async def test_compute_clears_buckets(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        scorer.feed_input(InputBucket(key_press_count=10))
        scorer.feed_input(InputBucket(key_press_count=20))

        with patch("aisisstant.scorer.get_idle_ms", return_value=0):
            await scorer._compute()

        assert len(scorer._input_buckets) == 0

    @pytest.mark.asyncio
    async def test_compute_aggregates_all_buckets(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        scorer.set_window("vim")

        scorer.feed_input(InputBucket(
            key_press_count=10,
            mouse_distance_px=100.0,
            mouse_click_left=2,
            mouse_click_right=1,
            scroll_distance=3,
        ))
        scorer.feed_input(InputBucket(
            key_press_count=15,
            mouse_distance_px=200.0,
            mouse_click_left=1,
            mouse_click_middle=1,
            scroll_distance=2,
        ))

        with patch("aisisstant.scorer.get_idle_ms", return_value=0):
            await scorer._compute()

        # Find the activity_scores call
        for call in mock_writer.put.call_args_list:
            table, record = call[0]
            if table == "activity_scores":
                assert record.key_presses == 25
                assert record.mouse_distance == 300.0
                assert record.clicks == 5  # 2+1+1+1
                assert record.scroll == 5
                assert record.wm_class == "vim"
                break
        else:
            pytest.fail("activity_scores not written")

    @pytest.mark.asyncio
    async def test_compute_unknown_wm_class_when_empty(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        # Don't set wm_class

        with patch("aisisstant.scorer.get_idle_ms", return_value=0):
            await scorer._compute()

        for call in mock_writer.put.call_args_list:
            table, record = call[0]
            if table == "activity_scores":
                assert record.wm_class == "unknown"
                break

    @pytest.mark.asyncio
    async def test_compute_with_idle(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        scorer.feed_input(InputBucket(key_press_count=60))

        with patch("aisisstant.scorer.get_idle_ms", return_value=30000):
            await scorer._compute()

        for call in mock_writer.put.call_args_list:
            table, record = call[0]
            if table == "activity_scores":
                assert record.score == 0.0
                assert record.score_label == "idle"
                break

    @pytest.mark.asyncio
    async def test_compute_mic_active_passed_through(self, mock_writer):
        scorer = ActivityScorer(mock_writer, score_window=30)
        scorer.set_mic(True)

        with patch("aisisstant.scorer.get_idle_ms", return_value=0):
            await scorer._compute()

        for call in mock_writer.put.call_args_list:
            table, record = call[0]
            if table == "activity_scores":
                assert record.mic_active is True
                break
