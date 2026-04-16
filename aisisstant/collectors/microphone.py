from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from ..models import MicState
from .base import BaseCollector

if TYPE_CHECKING:
    from ..db import BatchWriter


class MicrophoneCollector(BaseCollector):
    name = "microphone"

    def __init__(self, writer: BatchWriter, poll_seconds: float = 10.0):
        super().__init__(writer)
        self.poll_seconds = poll_seconds
        self._last_active = False

    async def run(self) -> None:
        self.log.info("Microphone collector started (poll every %.1fs)", self.poll_seconds)
        while True:
            try:
                state = await self._poll_pipewire()
                # Only log on state change or if active
                if state.is_active or state.is_active != self._last_active:
                    await self.writer.put("mic_activity", state)
                    if state.is_active != self._last_active:
                        self.log.info(
                            "Mic %s (app=%s)",
                            "ACTIVE" if state.is_active else "INACTIVE",
                            state.client_app,
                        )
                self._last_active = state.is_active
            except Exception:
                self.log.exception("Error polling microphone")
            await asyncio.sleep(self.poll_seconds)

    async def _poll_pipewire(self) -> MicState:
        proc = await asyncio.create_subprocess_exec(
            "pw-dump",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return MicState()

        nodes = json.loads(stdout.decode())

        # Find audio source nodes
        sources: dict[int, dict] = {}
        capture_streams: list[dict] = []
        links: list[dict] = []

        for node in nodes:
            node_type = node.get("type", "")
            props = node.get("info", {}).get("props", {})
            media_class = props.get("media.class", "")

            if media_class == "Audio/Source":
                sources[node["id"]] = node
            elif media_class == "Stream/Input/Audio":
                capture_streams.append(node)
            elif node_type == "PipeWire:Interface:Link":
                links.append(node)

        # Check if any source is linked to a capture stream
        capture_ids = {s["id"] for s in capture_streams}
        source_ids = set(sources.keys())

        for link in links:
            link_info = link.get("info", {})
            out_node = link_info.get("output-node-id")
            in_node = link_info.get("input-node-id")

            if out_node in source_ids and in_node in capture_ids:
                # Found active mic capture
                stream = next(
                    (s for s in capture_streams if s["id"] == in_node), None
                )
                if stream:
                    sprops = stream.get("info", {}).get("props", {})
                    src_props = sources[out_node].get("info", {}).get("props", {})
                    return MicState(
                        is_active=True,
                        source_node=src_props.get("node.name", ""),
                        client_app=sprops.get("application.name", ""),
                        client_pid=int(sprops.get("application.process.id", 0)),
                    )

        return MicState(is_active=False)
