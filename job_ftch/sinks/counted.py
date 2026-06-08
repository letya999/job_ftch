"""Sink wrapper that records emit counts for summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from job_ftch.application.contracts import Sink

ItemT = TypeVar("ItemT")


class CountedSink[ItemT]:
    def __init__(self, sink: Sink[ItemT]) -> None:
        self._sink = sink
        self.emit_count = 0
        self.by_source_kind: dict[str, int] = {}

    async def emit(self, item: ItemT) -> None:
        self.emit_count += 1
        source_kind = str(getattr(item, "source_kind", "unknown"))
        self.by_source_kind[source_kind] = self.by_source_kind.get(source_kind, 0) + 1
        await self._sink.emit(item)

    async def flush(self) -> None:
        if hasattr(self._sink, "flush"):
            await self._sink.flush()
