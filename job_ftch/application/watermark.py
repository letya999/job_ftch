"""Unified incremental cursor helpers backed by StoreConnector."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import StoreConnector


class IncrementalCursor:
    """Tenant/source-scoped watermark storage on top of StoreConnector."""

    def __init__(self, connector: StoreConnector, *, namespace: str = "") -> None:
        self._connector = connector
        self._namespace = namespace.strip(":")
        self._pending: ContextVar[dict[str, str] | None] = ContextVar(
            "pending_cursors", default=None
        )

    def _key(self, source_id: str) -> str:
        if self._namespace:
            return f"{self._namespace}:{source_id}:cursor"
        return f"{source_id}:cursor"

    async def get(self, source_id: str) -> str | None:
        key = self._key(source_id)
        pending = self._pending.get()
        if pending is not None and key in pending:
            return pending[key]
        return await self._connector.get(key)

    async def set(self, source_id: str, cursor: str) -> None:
        pending = self._pending.get()
        if pending is not None:
            pending[self._key(source_id)] = cursor
        else:
            await self._connector.set(self._key(source_id), cursor)

    @asynccontextmanager
    async def defer(self) -> AsyncIterator[dict[str, str]]:
        pending: dict[str, str] = {}
        token = self._pending.set(pending)
        try:
            yield pending
        except BaseException:
            raise
        else:
            for key, value in pending.items():
                await self._connector.set(key, value)
        finally:
            self._pending.reset(token)

    async def reset(self, source_id: str) -> None:
        await self._connector.delete(self._key(source_id))
