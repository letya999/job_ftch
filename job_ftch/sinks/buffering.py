"""Buffering sink that collects items in memory for batch post-processing."""
from __future__ import annotations


class BufferSink[T]:
    """Collects emitted items into an in-memory list.

    Used for batch reranking: collect all matched jobs, rerank, then forward.
    """

    def __init__(self) -> None:
        self._items: list[T] = []

    async def emit(self, item: T) -> None:
        self._items.append(item)

    @property
    def items(self) -> list[T]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
