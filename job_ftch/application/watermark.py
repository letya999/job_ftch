"""Unified incremental cursor helpers backed by StoreConnector."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from job_ftch.application.contracts import StoreConnector


class IncrementalCursor:
    """Tenant/source-scoped watermark storage on top of StoreConnector."""

    def __init__(self, connector: StoreConnector, *, namespace: str = "") -> None:
        self._connector = connector
        self._namespace = namespace.strip(":")

    def _key(self, source_id: str) -> str:
        if self._namespace:
            return f"{self._namespace}:{source_id}:cursor"
        return f"{source_id}:cursor"

    async def get(self, source_id: str) -> str | None:
        return await self._connector.get(self._key(source_id))

    async def set(self, source_id: str, cursor: str) -> None:
        await self._connector.set(self._key(source_id), cursor)

    async def reset(self, source_id: str) -> None:
        await self._connector.delete(self._key(source_id))
