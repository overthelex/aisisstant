from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db import BatchWriter


class BaseCollector(abc.ABC):
    """Abstract base for all data collectors."""

    name: str = "base"

    def __init__(self, writer: BatchWriter):
        self.writer = writer
        self.log = logging.getLogger(f"aisisstant.collectors.{self.name}")

    @abc.abstractmethod
    async def run(self) -> None:
        ...
