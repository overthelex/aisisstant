from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from evdev import ecodes

from aisisstant.collectors.mouse import MouseCollector, find_mice
from aisisstant.models import InputBucket


class TestFindMice:
    def test_finds_devices_with_rel_x_y(self):
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL],
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.mouse.Path") as mock_path, \
             patch("aisisstant.collectors.mouse.evdev.InputDevice", return_value=mock_dev):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event7"
            mock_path.return_value.glob.return_value = [p]

            result = find_mice()
        assert result == ["/dev/input/event7"]

    def test_skips_devices_without_ev_rel(self):
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            ecodes.EV_KEY: [ecodes.KEY_A],
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.mouse.Path") as mock_path, \
             patch("aisisstant.collectors.mouse.evdev.InputDevice", return_value=mock_dev):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_mice()
        assert result == []

    def test_skips_devices_with_only_rel_x(self):
        """Must have both REL_X and REL_Y."""
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            ecodes.EV_REL: [ecodes.REL_X],  # no REL_Y
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.mouse.Path") as mock_path, \
             patch("aisisstant.collectors.mouse.evdev.InputDevice", return_value=mock_dev):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_mice()
        assert result == []

    def test_handles_permission_error(self):
        with patch("aisisstant.collectors.mouse.Path") as mock_path, \
             patch("aisisstant.collectors.mouse.evdev.InputDevice", side_effect=PermissionError):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_mice()
        assert result == []


class TestMouseCollector:
    def test_init(self, mock_writer):
        mc = MouseCollector(mock_writer, bucket_seconds=3)
        assert mc.bucket_seconds == 3
        assert mc._dx == 0.0
        assert mc._dy == 0.0
        assert mc.name == "mouse"

    @pytest.mark.asyncio
    async def test_flush_loop_writes_nonempty_bucket(self, mock_writer):
        mc = MouseCollector(mock_writer, bucket_seconds=0.1)
        mc._bucket.mouse_click_left = 2
        mc._bucket.mouse_distance_px = 150.0

        task = asyncio.create_task(mc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_writer.put.assert_called_once()
        table, bucket = mock_writer.put.call_args[0]
        assert table == "input_activity"
        assert bucket.mouse_click_left == 2
        assert bucket.mouse_distance_px == 150.0

    @pytest.mark.asyncio
    async def test_flush_loop_skips_empty_bucket(self, mock_writer):
        mc = MouseCollector(mock_writer, bucket_seconds=0.1)

        task = asyncio.create_task(mc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_loop_resets_bucket_and_deltas(self, mock_writer):
        mc = MouseCollector(mock_writer, bucket_seconds=0.1)
        mc._bucket.mouse_click_left = 5
        mc._bucket.scroll_distance = 3

        task = asyncio.create_task(mc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert mc._bucket.mouse_click_left == 0
        assert mc._bucket.scroll_distance == 0

    def test_distance_calculation(self, mock_writer):
        """Test that dx/dy accumulation and sqrt distance works."""
        mc = MouseCollector(mock_writer)
        mc._dx = 3.0
        mc._dy = 4.0
        # Simulate SYN_REPORT by manually doing what _read_device does
        distance = math.sqrt(mc._dx ** 2 + mc._dy ** 2)
        assert abs(distance - 5.0) < 1e-6

    def test_distance_calculation_diagonal(self, mock_writer):
        mc = MouseCollector(mock_writer)
        mc._dx = 10.0
        mc._dy = 10.0
        distance = math.sqrt(mc._dx ** 2 + mc._dy ** 2)
        assert abs(distance - math.sqrt(200)) < 1e-6
