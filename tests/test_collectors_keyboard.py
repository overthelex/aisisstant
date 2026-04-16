from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from aisisstant.collectors.keyboard import KeyboardCollector, find_keyboards
from aisisstant.models import InputBucket


class TestFindKeyboards:
    def test_finds_devices_with_letter_keys(self):
        """Mock a device that has EV_KEY with letter keycodes."""
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            1: list(range(1, 60)),  # EV_KEY=1, keycodes 1-59 (includes 30-44)
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.keyboard.Path") as mock_path, \
             patch("aisisstant.collectors.keyboard.evdev.InputDevice", return_value=mock_dev):
            mock_path.return_value.glob.return_value = [
                MagicMock(__str__=lambda self: "/dev/input/event0")
            ]
            # Need to make str(path) return the right thing
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_keyboards()
        assert len(result) == 1
        assert result[0] == "/dev/input/event0"

    def test_skips_devices_without_ev_key(self):
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            2: [0, 1],  # EV_REL only
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.keyboard.Path") as mock_path, \
             patch("aisisstant.collectors.keyboard.evdev.InputDevice", return_value=mock_dev):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_keyboards()
        assert result == []

    def test_skips_devices_without_letter_keys(self):
        """Device with EV_KEY but no letter keycodes (e.g., power button)."""
        mock_dev = MagicMock()
        mock_dev.capabilities.return_value = {
            1: [116, 142, 143],  # KEY_POWER, KEY_SLEEP, KEY_WAKEUP
        }
        mock_dev.close = MagicMock()

        with patch("aisisstant.collectors.keyboard.Path") as mock_path, \
             patch("aisisstant.collectors.keyboard.evdev.InputDevice", return_value=mock_dev):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_keyboards()
        assert result == []

    def test_handles_permission_error(self):
        with patch("aisisstant.collectors.keyboard.Path") as mock_path, \
             patch("aisisstant.collectors.keyboard.evdev.InputDevice", side_effect=PermissionError):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_keyboards()
        assert result == []

    def test_handles_os_error(self):
        with patch("aisisstant.collectors.keyboard.Path") as mock_path, \
             patch("aisisstant.collectors.keyboard.evdev.InputDevice", side_effect=OSError("no device")):
            p = MagicMock()
            p.__str__ = lambda self: "/dev/input/event0"
            mock_path.return_value.glob.return_value = [p]

            result = find_keyboards()
        assert result == []


class TestKeyboardCollector:
    def test_init(self, mock_writer):
        kc = KeyboardCollector(mock_writer, bucket_seconds=10)
        assert kc.bucket_seconds == 10
        assert kc._bucket.key_press_count == 0
        assert kc.name == "keyboard"

    @pytest.mark.asyncio
    async def test_flush_loop_writes_nonempty_bucket(self, mock_writer):
        kc = KeyboardCollector(mock_writer, bucket_seconds=0.1)
        kc._bucket.key_press_count = 5

        # Run flush loop briefly
        task = asyncio.create_task(kc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_writer.put.assert_called_once()
        table, bucket = mock_writer.put.call_args[0]
        assert table == "input_activity"
        assert bucket.key_press_count == 5
        assert bucket.bucket_end is not None

    @pytest.mark.asyncio
    async def test_flush_loop_skips_empty_bucket(self, mock_writer):
        kc = KeyboardCollector(mock_writer, bucket_seconds=0.1)
        # bucket is empty by default

        task = asyncio.create_task(kc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_loop_resets_bucket(self, mock_writer):
        kc = KeyboardCollector(mock_writer, bucket_seconds=0.1)
        kc._bucket.key_press_count = 10

        task = asyncio.create_task(kc._flush_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # New bucket should be empty
        assert kc._bucket.key_press_count == 0
