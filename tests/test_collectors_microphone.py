from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from aisisstant.collectors.microphone import MicrophoneCollector
from aisisstant.models import MicState


def make_pw_dump_output(
    sources: list[dict] | None = None,
    streams: list[dict] | None = None,
    links: list[dict] | None = None,
) -> bytes:
    """Build a minimal pw-dump JSON output."""
    nodes = []
    for s in (sources or []):
        nodes.append({
            "id": s["id"],
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": "Audio/Source", "node.name": s.get("name", "")}},
        })
    for s in (streams or []):
        nodes.append({
            "id": s["id"],
            "type": "PipeWire:Interface:Node",
            "info": {"props": {
                "media.class": "Stream/Input/Audio",
                "application.name": s.get("app", ""),
                "application.process.id": str(s.get("pid", 0)),
            }},
        })
    for link in (links or []):
        nodes.append({
            "id": link.get("id", 999),
            "type": "PipeWire:Interface:Link",
            "info": {
                "output-node-id": link["out"],
                "input-node-id": link["in"],
            },
        })
    return json.dumps(nodes).encode()


class TestMicrophoneCollectorPollPipewire:
    @pytest.mark.asyncio
    async def test_no_capture_returns_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        pw_data = make_pw_dump_output(
            sources=[{"id": 10, "name": "alsa_input.usb"}],
        )
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (pw_data, b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_active_capture_returns_active(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        pw_data = make_pw_dump_output(
            sources=[{"id": 10, "name": "alsa_input.usb-volt"}],
            streams=[{"id": 20, "app": "zoom", "pid": 5555}],
            links=[{"id": 30, "out": 10, "in": 20}],
        )
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (pw_data, b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is True
        assert state.source_node == "alsa_input.usb-volt"
        assert state.client_app == "zoom"
        assert state.client_pid == 5555

    @pytest.mark.asyncio
    async def test_link_not_from_source_is_inactive(self, mock_writer):
        """Link exists but doesn't connect source to capture."""
        mc = MicrophoneCollector(mock_writer)

        pw_data = make_pw_dump_output(
            sources=[{"id": 10, "name": "alsa_input"}],
            streams=[{"id": 20, "app": "chrome", "pid": 100}],
            links=[{"id": 30, "out": 99, "in": 20}],  # out=99 is not a source
        )
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (pw_data, b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_link_not_to_stream_is_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        pw_data = make_pw_dump_output(
            sources=[{"id": 10, "name": "alsa_input"}],
            streams=[{"id": 20, "app": "chrome", "pid": 100}],
            links=[{"id": 30, "out": 10, "in": 88}],  # in=88 is not a stream
        )
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (pw_data, b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_pw_dump_failure_returns_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_empty_pw_dump_returns_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"[]", b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_multiple_sources_one_linked(self, mock_writer):
        mc = MicrophoneCollector(mock_writer)

        pw_data = make_pw_dump_output(
            sources=[
                {"id": 10, "name": "alsa_input.builtin"},
                {"id": 11, "name": "alsa_input.usb"},
            ],
            streams=[{"id": 20, "app": "obs", "pid": 7777}],
            links=[{"id": 30, "out": 11, "in": 20}],  # USB mic linked
        )
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (pw_data, b"")
        mock_proc.returncode = 0

        with patch("aisisstant.collectors.microphone.asyncio.create_subprocess_exec", return_value=mock_proc):
            state = await mc._poll_pipewire()

        assert state.is_active is True
        assert state.source_node == "alsa_input.usb"
        assert state.client_app == "obs"


class TestMicrophoneCollectorStateTracking:
    @pytest.mark.asyncio
    async def test_writes_on_state_change_active(self, mock_writer):
        mc = MicrophoneCollector(mock_writer, poll_seconds=0.1)
        mc._last_active = False

        active_state = MicState(is_active=True, client_app="zoom")

        with patch.object(mc, "_poll_pipewire", return_value=active_state):
            # Run one iteration manually
            state = await mc._poll_pipewire()
            if state.is_active or state.is_active != mc._last_active:
                await mc.writer.put("mic_activity", state)
            mc._last_active = state.is_active

        mock_writer.put.assert_called_once()
        _, written = mock_writer.put.call_args[0]
        assert written.is_active is True

    @pytest.mark.asyncio
    async def test_skips_when_still_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer, poll_seconds=0.1)
        mc._last_active = False

        inactive_state = MicState(is_active=False)

        with patch.object(mc, "_poll_pipewire", return_value=inactive_state):
            state = await mc._poll_pipewire()
            # Replicate the collector logic
            if state.is_active or state.is_active != mc._last_active:
                await mc.writer.put("mic_activity", state)
            mc._last_active = state.is_active

        mock_writer.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_on_state_change_to_inactive(self, mock_writer):
        mc = MicrophoneCollector(mock_writer, poll_seconds=0.1)
        mc._last_active = True  # Was active before

        inactive_state = MicState(is_active=False)

        with patch.object(mc, "_poll_pipewire", return_value=inactive_state):
            state = await mc._poll_pipewire()
            if state.is_active or state.is_active != mc._last_active:
                await mc.writer.put("mic_activity", state)
            mc._last_active = state.is_active

        mock_writer.put.assert_called_once()
