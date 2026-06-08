"""PostgreSQL implementation of the persistent store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

from job_ftch.application.registry import register_store
from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter

if TYPE_CHECKING:
    from job_ftch.config import Settings


class PostgreSQLStore(SQLStoreAdapter):
    """PostgreSQL-backed persistent store using asyncpg."""

    _SQL_KV_GET = "SELECT value FROM jf_kv WHERE key = $1"
    _SQL_KV_UPSERT = """
        INSERT INTO jf_kv (key, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """
    _SQL_KV_DELETE = "DELETE FROM jf_kv WHERE key = $1"
    _SQL_SET_ADD = "INSERT INTO jf_set (key, member) VALUES ($1, $2) ON CONFLICT DO NOTHING"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = $1 AND member = $2"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = $1"

    def __init__(
        self,
        dsn: str,
        pool_min: int = 2,
        pool_max: int = 10,
    ) -> None:
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if asyncpg is None:
            raise ImportError(
                "asyncpg is required for PostgreSQLStore. Install with [postgres] extra."
            )

        async with self._init_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=self._pool_min, max_size=self._pool_max
                )
                await self._initialize()
        return self._pool

    async def _initialize(self) -> None:
        schema_path = Path(__file__).parent / "migrations" / "001_initial_schema_pg.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Migration file not found: {schema_path}")

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(schema_path.read_text())

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def _fetchone(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return tuple(row) if row else None

    async def _fetchall(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [tuple(row) for row in rows]

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        """Check connection health."""
        try:
            await self._fetchone("SELECT 1")
            return True
        except Exception:
            return False


@register_store("postgres")
def _build_postgres_store(settings: Settings) -> PostgreSQLStore:
    if not settings.store_dsn:
        raise ValueError("store_dsn is required for PostgreSQLStore")
    return PostgreSQLStore(
        dsn=settings.store_dsn,
        pool_min=settings.store_pool_min,
        pool_max=settings.store_pool_max,
    )
