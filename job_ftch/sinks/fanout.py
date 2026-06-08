"""Sink fan-out for multiple output backends."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.application.contracts import Sink

ItemT = TypeVar("ItemT")


class FanOutSink[ItemT]:
    def __init__(self, sinks: Sequence[Sink[ItemT]]) -> None:
        self._sinks = list(sinks)

    async def emit(self, item: ItemT) -> None:
        for sink in self._sinks:
            await sink.emit(item)

    async def flush(self) -> None:
        for sink in self._sinks:
            if hasattr(sink, "flush"):
                await sink.flush()
