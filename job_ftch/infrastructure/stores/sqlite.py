"""SQLite implementation of the persistent store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]

from job_ftch.application.registry import register_store
from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter

if TYPE_CHECKING:
    from job_ftch.config import Settings


class SQLiteStore(SQLStoreAdapter):
    """SQLite-backed persistent store using aiosqlite."""

    _SQL_KV_GET = "SELECT value FROM jf_kv WHERE key = ?"
    _SQL_KV_UPSERT = """
        INSERT INTO jf_kv (key, value, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """
    _SQL_KV_DELETE = "DELETE FROM jf_kv WHERE key = ?"
    _SQL_SET_ADD = "INSERT OR IGNORE INTO jf_set (key, member) VALUES (?, ?)"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = ? AND member = ?"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = ?"

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if aiosqlite is None:
            raise ImportError("aiosqlite is required for SQLiteStore. Install with [sqlite] extra.")

        async with self._init_lock:
            if self._conn is None:
                self._conn = await aiosqlite.connect(self._path)
                self._conn.row_factory = aiosqlite.Row
                await self._initialize()
        return self._conn

    async def _initialize(self) -> None:
        schema_path = Path(__file__).parent / "migrations" / "001_initial_schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Migration file not found: {schema_path}")

        # executescript() issues an implicit COMMIT before running, so no separate
        # commit is needed. Only call this during initialization (no pending txns).
        await self._conn.executescript(schema_path.read_text())  # type: ignore[union-attr]

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        conn = await self._ensure_initialized()
        await conn.execute(sql, params)
        await conn.commit()

    async def _fetchone(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        conn = await self._ensure_initialized()
        async with conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return tuple(row) if row else None

    async def _fetchall(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        conn = await self._ensure_initialized()
        async with conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [tuple(row) for row in rows]

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        """Check connection health."""
        try:
            await self._fetchone("SELECT 1")
            return True
        except Exception:
            return False


@register_store("sqlite")
def _build_sqlite_store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(path=settings.store_path)
