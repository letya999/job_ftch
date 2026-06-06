"""Conditional sink routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from application.contracts import Sink

ItemT = TypeVar("ItemT")


class RoutingSink[ItemT]:
    def __init__(
        self,
        routes: Sequence[tuple[Callable[[ItemT], bool], Sink[ItemT]]],
        *,
        fallback: Sink[ItemT] | None = None,
    ) -> None:
        self._routes = list(routes)
        self._fallback = fallback

    async def emit(self, item: ItemT) -> None:
        for predicate, sink in self._routes:
            if predicate(item):
                await sink.emit(item)
                return
        if self._fallback is not None:
            await self._fallback.emit(item)

    async def flush(self) -> None:
        seen: list[object] = [sink for _, sink in self._routes]
        if self._fallback is not None:
            seen.append(self._fallback)
        for sink in seen:
            if hasattr(sink, "flush"):
                await sink.flush()
