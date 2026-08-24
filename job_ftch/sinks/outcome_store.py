"""Opt-in store-backed sink for compact operational outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


Lane = Literal["review", "rejected"]


class OutcomeRecorder(Protocol):
    async def record_operational_outcome(self, lane: str, payload: Mapping[str, Any]) -> None: ...


class StoreOutcomeSink:
    """Write compact outcome rows into a tenant store when enabled."""

    def __init__(self, store: OutcomeRecorder, *, lane: Lane) -> None:
        self._store = store
        self._lane = lane

    async def emit(self, item: object) -> None:
        if isinstance(item, dict):
            payload: dict[str, Any] = dict(item)
        elif hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        else:
            payload = {"value": str(item)}
        payload.setdefault("lane", self._lane)
        await self._store.record_operational_outcome(self._lane, payload)

    async def flush(self) -> None:
        return None
