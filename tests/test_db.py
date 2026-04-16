from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aisisstant.db import BatchWriter
from aisisstant.models import (
    ActivityScore,
    IdleEvent,
    InputBucket,
    MicState,
    WindowSession,
    _now,
)


class TestBatchWriterPut:
    @pytest.mark.asyncio
    async def test_put_adds_to_queue(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)
        bucket = InputBucket(key_press_count=5)

        await writer.put("input_activity", bucket)

        assert writer._queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_put_multiple_items(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)

        await writer.put("input_activity", InputBucket(key_press_count=1))
        await writer.put("input_activity", InputBucket(key_press_count=2))
        await writer.put("mic_activity", MicState(is_active=True))

        assert writer._queue.qsize() == 3


class TestBatchWriterFlush:
    @pytest.mark.asyncio
    async def test_flush_groups_by_table(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)

        now = _now()
        items = [
            ("input_activity", InputBucket(
                bucket_start=now,
                bucket_end=now + timedelta(seconds=5),
                key_press_count=10,
            )),
            ("input_activity", InputBucket(
                bucket_start=now,
                bucket_end=now + timedelta(seconds=5),
                key_press_count=20,
            )),
            ("mic_activity", MicState(is_active=True, client_app="zoom")),
        ]

        mock_conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await writer._flush(items)

        # Should have called executemany for input_activity and mic_activity
        assert mock_conn.executemany.call_count == 2

    @pytest.mark.asyncio
    async def test_flush_handles_all_table_types(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)
        now = _now()

        items = [
            ("input_activity", InputBucket(
                bucket_start=now, bucket_end=now + timedelta(seconds=5),
            )),
            ("window_sessions", WindowSession(
                wm_class="firefox", window_title="Home", pid=100,
            )),
            ("mic_activity", MicState(is_active=False)),
            ("activity_scores", ActivityScore(
                window_start=now, window_end=now, wm_class="x",
                score=0.5, score_label="active",
            )),
            ("idle_events", IdleEvent(timestamp=now, idle_ms=1000)),
        ]

        mock_conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await writer._flush(items)

        # 5 different tables, each should call executemany
        assert mock_conn.executemany.call_count == 5

    @pytest.mark.asyncio
    async def test_flush_handles_window_session_close(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)
        now = _now()

        items = [
            ("window_session_close", {
                "ended_at": now,
                "wm_class": "firefox",
                "started_at": now - timedelta(minutes=5),
            }),
        ]

        mock_conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await writer._flush(items)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE window_sessions" in call_args[0][0]
        assert call_args[0][1] == now
        assert call_args[0][2] == "firefox"

    @pytest.mark.asyncio
    async def test_flush_logs_exception_and_continues(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=1.0)
        now = _now()

        items = [
            ("input_activity", InputBucket(
                bucket_start=now, bucket_end=now + timedelta(seconds=5),
            )),
            ("mic_activity", MicState(is_active=False)),
        ]

        mock_conn = AsyncMock()
        # First call fails, second succeeds
        mock_conn.executemany.side_effect = [Exception("db error"), None]
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await writer._flush(items)
        assert mock_conn.executemany.call_count == 2


class TestBatchWriterRun:
    @pytest.mark.asyncio
    async def test_run_drains_queue_periodically(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=0.1)

        now = _now()
        await writer.put("mic_activity", MicState(is_active=True))

        with patch.object(writer, "_flush", new_callable=AsyncMock) as mock_flush:
            task = asyncio.create_task(writer.run())
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_flush.assert_called_once()
            items = mock_flush.call_args[0][0]
            assert len(items) == 1
            assert items[0][0] == "mic_activity"

    @pytest.mark.asyncio
    async def test_run_skips_when_queue_empty(self):
        pool = MagicMock()
        writer = BatchWriter(pool, flush_interval=0.1)

        with patch.object(writer, "_flush", new_callable=AsyncMock) as mock_flush:
            task = asyncio.create_task(writer.run())
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_flush.assert_not_called()


class TestBatchWriterInsertMethods:
    """Test the SQL parameter mapping for each insert method."""

    @pytest.mark.asyncio
    async def test_insert_input_activity_params(self):
        pool = MagicMock()
        writer = BatchWriter(pool)
        now = _now()
        end = now + timedelta(seconds=5)

        bucket = InputBucket(
            bucket_start=now,
            bucket_end=end,
            key_press_count=42,
            mouse_distance_px=123.4,
            mouse_click_left=3,
            mouse_click_right=1,
            mouse_click_middle=0,
            scroll_distance=7,
        )

        mock_conn = AsyncMock()
        await writer._insert_input_activity(mock_conn, [bucket])

        mock_conn.executemany.assert_called_once()
        args = mock_conn.executemany.call_args
        params = args[0][1][0]
        assert params[0] == now      # bucket_start
        assert params[1] == end      # bucket_end
        assert params[2] == 42       # key_press_count
        assert abs(params[3] - 42 / 5.0) < 1e-4  # key_rate_per_sec
        assert params[4] == 123.4    # mouse_distance_px
        assert params[5] == 3        # left
        assert params[6] == 1        # right
        assert params[7] == 0        # middle
        assert params[8] == 7        # scroll

    @pytest.mark.asyncio
    async def test_insert_window_sessions_params(self):
        pool = MagicMock()
        writer = BatchWriter(pool)
        now = _now()

        session = WindowSession(
            wm_class="code", window_title="main.py", pid=999, started_at=now,
        )

        mock_conn = AsyncMock()
        await writer._insert_window_sessions(mock_conn, [session])

        params = mock_conn.executemany.call_args[0][1][0]
        assert params[0] == now
        assert params[1] == "code"
        assert params[2] == "main.py"
        assert params[3] == 999

    @pytest.mark.asyncio
    async def test_insert_mic_activity_params(self):
        pool = MagicMock()
        writer = BatchWriter(pool)

        state = MicState(
            is_active=True, source_node="node1", client_app="zoom", client_pid=5555,
        )

        mock_conn = AsyncMock()
        await writer._insert_mic_activity(mock_conn, [state])

        params = mock_conn.executemany.call_args[0][1][0]
        assert params[0] is True
        assert params[1] == "node1"
        assert params[2] == "zoom"
        assert params[3] == 5555

    @pytest.mark.asyncio
    async def test_insert_activity_scores_params(self):
        pool = MagicMock()
        writer = BatchWriter(pool)
        now = _now()

        score = ActivityScore(
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

        mock_conn = AsyncMock()
        await writer._insert_activity_scores(mock_conn, [score])

        params = mock_conn.executemany.call_args[0][1][0]
        assert params[3] == ""
        assert params[4] == 0.75
        assert params[5] == "active"
        assert params[6] == 100
        assert params[10] is True

    @pytest.mark.asyncio
    async def test_insert_idle_events_params(self):
        pool = MagicMock()
        writer = BatchWriter(pool)
        now = _now()

        event = IdleEvent(timestamp=now, idle_ms=5000, is_locked=True)

        mock_conn = AsyncMock()
        await writer._insert_idle_events(mock_conn, [event])

        params = mock_conn.executemany.call_args[0][1][0]
        assert params[0] == now
        assert params[1] == 5000
        assert params[2] is True
