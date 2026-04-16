from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from aisisstant.models import InputBucket, WindowInfo, WindowSession, MicState, _now


@pytest.fixture
def mock_writer():
    """A mock BatchWriter that records all put() calls."""
    writer = AsyncMock()
    writer.put = AsyncMock()
    writer.pool = MagicMock()
    return writer


@pytest.fixture
def sample_bucket():
    """An InputBucket with some activity."""
    start = datetime(2026, 4, 16, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=5)
    return InputBucket(
        bucket_start=start,
        bucket_end=end,
        key_press_count=25,
        mouse_distance_px=500.0,
        mouse_click_left=3,
        mouse_click_right=1,
        mouse_click_middle=0,
        scroll_distance=10,
    )


@pytest.fixture
def empty_bucket():
    """An InputBucket with no activity."""
    start = datetime(2026, 4, 16, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=5)
    return InputBucket(
        bucket_start=start,
        bucket_end=end,
    )
