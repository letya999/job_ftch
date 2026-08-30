"""PostgreSQL implementation of the persistent store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

try:
    import asyncpg

    _IMPORT_ERROR = None
except ImportError as exc:
    asyncpg = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

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
    _SQL_SET_CLEAR = "DELETE FROM jf_set WHERE key = $1"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = $1 AND member = $2"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = $1"
    _SQL_OUTBOX_ENQUEUE = "INSERT INTO jf_outbox (outbox_id, tenant_id, idempotency_key, state, payload_json) VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (idempotency_key) DO NOTHING"
    _SQL_OUTBOX_GET = "SELECT payload_json::text, state FROM jf_outbox WHERE idempotency_key = $1"
    _SQL_OUTBOX_PENDING = "SELECT payload_json::text, state FROM jf_outbox WHERE state = $1 AND tenant_id = $2 ORDER BY updated_at ASC LIMIT $3"
    _SQL_OUTBOX_DELIVERED = (
        "UPDATE jf_outbox SET state = $1, updated_at = NOW() WHERE idempotency_key = $2"
    )
    _SQL_DEDUP_CLAIM_ACQUIRE = """
        INSERT INTO jf_dedup_claims (claim_key, owner_id, expires_at)
        VALUES ($1, $2, NOW() + ($3::int * INTERVAL '1 second'))
        ON CONFLICT(claim_key) DO UPDATE SET owner_id=EXCLUDED.owner_id, expires_at=EXCLUDED.expires_at
        WHERE jf_dedup_claims.expires_at <= NOW()
    """
    _SQL_DEDUP_CLAIM_OWNER = "SELECT owner_id FROM jf_dedup_claims WHERE claim_key = $1"
    _SQL_DEDUP_CLAIM_RELEASE = "DELETE FROM jf_dedup_claims WHERE claim_key = $1 AND owner_id = $2"
    _SQL_OBSERVATION_GET = "SELECT payload_json::text FROM jf_observations WHERE tenant_id = $1 AND stable_id = $2 AND content_hash = $3"
    _SQL_OBSERVATION_MAX_VERSION = (
        "SELECT MAX(content_version) FROM jf_observations WHERE tenant_id = $1 AND stable_id = $2"
    )
    _SQL_OBSERVATION_INSERT = "INSERT INTO jf_observations (tenant_id, stable_id, content_hash, content_version, payload_json) VALUES ($1, $2, $3, $4, $5::jsonb)"

    # ADR-031: source snapshot table SQL (PostgreSQL)
    _SQL_SNAPSHOT_LAST_RUN_IDS = """
        SELECT stable_id FROM jf_source_snapshots
        WHERE tenant_id = $1 AND source_id = $2
          AND run_id = (
              SELECT run_id FROM jf_source_snapshots
              WHERE tenant_id = $3 AND source_id = $4
              ORDER BY run_at DESC LIMIT 1
          )
    """
    _SQL_SNAPSHOT_LAST_RUN_HASHES = """
        SELECT stable_id, item_hash FROM jf_source_snapshots
        WHERE tenant_id = $1 AND source_id = $2 AND run_id = (
            SELECT run_id FROM jf_source_snapshots
            WHERE tenant_id = $3 AND source_id = $4 ORDER BY run_at DESC LIMIT 1
        )
    """
    _SQL_SNAPSHOT_INSERT = """
        INSERT INTO jf_source_snapshots
            (tenant_id, source_id, run_id, stable_id, item_hash, item_json, run_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
    """
    _SQL_SNAPSHOT_PURGE = """
        WITH deleted AS (
            DELETE FROM jf_source_snapshots
            WHERE tenant_id = $1 AND source_id = $2
              AND run_at < NOW() - ($3::int * INTERVAL '1 day')
            RETURNING 1
        )
        SELECT COUNT(*)::int FROM deleted
    """
    _SQL_SNAPSHOT_PURGE_COUNT = "SELECT 1 WHERE FALSE"
    _SQL_SOURCE_ASSESSMENT_GET = """
        SELECT payload_json::text FROM jf_source_assessments
        WHERE tenant_id = $1 AND source_id = $2
    """
    _SQL_SOURCE_ASSESSMENT_UPSERT = """
        INSERT INTO jf_source_assessments
            (tenant_id, source_id, source_type, schema_version, assessed_at, payload_json, updated_at)
        VALUES ($1, $2, $3, $4, $5::timestamptz, $6::jsonb, NOW())
        ON CONFLICT (tenant_id, source_id) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            schema_version = EXCLUDED.schema_version,
            assessed_at = EXCLUDED.assessed_at,
            payload_json = EXCLUDED.payload_json,
            updated_at = EXCLUDED.updated_at
    """
    _SQL_SOURCE_INGEST_STATE_GET = """
        SELECT payload_json::text FROM jf_source_ingest_state
        WHERE tenant_id = $1 AND source_id = $2
    """
    _SQL_SOURCE_INGEST_STATE_UPSERT = """
        INSERT INTO jf_source_ingest_state
            (tenant_id, source_id, bootstrap_completed_at, payload_json, updated_at)
        VALUES ($1, $2, $3::timestamptz, $4::jsonb, $5::timestamptz)
        ON CONFLICT (tenant_id, source_id) DO UPDATE SET
            bootstrap_completed_at = EXCLUDED.bootstrap_completed_at,
            payload_json = EXCLUDED.payload_json,
            updated_at = EXCLUDED.updated_at
    """

    def __init__(
        self,
        dsn: str,
        pool_min: int = 2,
        pool_max: int = 10,
        *,
        processed_item_ttl_hours: int | None = 24,
    ) -> None:
        super().__init__(processed_item_ttl_hours=processed_item_ttl_hours)
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if asyncpg is None:
            raise ImportError(
                "PostgreSQL backend requires the 'postgres' extra: pip install job-ftch[postgres]"
            ) from _IMPORT_ERROR

        async with self._init_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=self._pool_min, max_size=self._pool_max
                )
                await self._initialize()
        return self._pool

    async def _initialize(self) -> None:
        migrations_dir = Path(__file__).parent / "migrations"
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            for name in (
                "001_initial_schema_pg.sql",
                "002_source_snapshots_pg.sql",
                "003_ontology_pg.sql",
                "004_source_assessment_pg.sql",
                "005_observation_ledger_pg.sql",
                "006_dedup_claims_pg.sql",
                "007_outbox_pg.sql",
                "008_ontology_provenance_pg.sql",
                "009_ontology_occurrences_pg.sql",
                "010_outbox_tenant_pg.sql",
                "011_ontology_graph_pg.sql",
                "012_ontology_term_stats_pg.sql",
                "013_compiled_ontology_pg.sql",
            ):
                path = migrations_dir / name
                if not path.exists():
                    continue
                await conn.execute(path.read_text())

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def _execute_batch(self, sql: str, params_list: tuple[tuple[object, ...], ...]) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(sql, params_list)

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

    async def purge_old_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        *,
        older_than_days: int,
    ) -> int:
        """Override for PostgreSQL: PURGE returns count via CTE in a single round trip."""
        if older_than_days <= 0:
            return 0
        row = await self._fetchone(
            self._SQL_SNAPSHOT_PURGE, (tenant_id, source_id, older_than_days)
        )
        if row is None:
            return 0
        try:
            return int(cast("int | str", row[0]))
        except (TypeError, ValueError):
            return 0

    async def reset_namespace(self, prefix: str) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM jf_kv WHERE key LIKE $1", f"{prefix}%")
            await conn.execute("DELETE FROM jf_set WHERE key LIKE $1", f"{prefix}%")

    async def clear_run_artifacts(self, prefix: str, tenant_id: str) -> dict[str, int]:
        """Remove run-produced state while preserving tenant configuration and profiles."""
        kv_patterns = tuple(
            f"{prefix}{suffix}"
            for suffix in (
                "relevance:%",
                "presentable:%",
                "processed_at:%",
                "dedup_record:%",
                "dup_record:%",
                "enrichment:%",
                "resolver:%",
                "pipeline.%",
                "snapshot:%",
                "source_health:%",
                "bot_publish:%",
                "bot_scheduler:last_publish%",
                "bot_scheduler:pending_publish_since",
                "outcome:%",
                "outcome_ids:%",
                "outcome_run_order:%",
            )
        )
        set_patterns = tuple(
            f"{prefix}{suffix}"
            for suffix in (
                "processed%",
                "dedup_keys%",
                "dup_records%",
                "source_health_ids",
                "outcome_ids:%",
            )
        )
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn, conn.transaction():
            counts = {
                "kv": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_kv WHERE key LIKE ANY($1::text[])",
                        kv_patterns,
                    )
                    or 0
                ),
                "sets": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_set WHERE key LIKE ANY($1::text[])",
                        set_patterns,
                    )
                    or 0
                ),
                "observations": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_observations WHERE tenant_id = $1", tenant_id
                    )
                    or 0
                ),
                "snapshots": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_source_snapshots WHERE tenant_id = $1", tenant_id
                    )
                    or 0
                ),
                "source_ingest_states": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_source_ingest_state WHERE tenant_id = $1",
                        tenant_id,
                    )
                    or 0
                ),
                "dedup_claims": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_dedup_claims WHERE claim_key LIKE $1",
                        f"{prefix}%",
                    )
                    or 0
                ),
                "outbox": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_outbox WHERE tenant_id = $1", tenant_id
                    )
                    or 0
                ),
                "source_assessments": int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM jf_source_assessments WHERE tenant_id = $1",
                        tenant_id,
                    )
                    or 0
                ),
            }
            await conn.execute("DELETE FROM jf_kv WHERE key LIKE ANY($1::text[])", kv_patterns)
            await conn.execute("DELETE FROM jf_set WHERE key LIKE ANY($1::text[])", set_patterns)
            await conn.execute("DELETE FROM jf_observations WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM jf_source_snapshots WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM jf_source_ingest_state WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM jf_dedup_claims WHERE claim_key LIKE $1", f"{prefix}%")
            await conn.execute("DELETE FROM jf_outbox WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM jf_source_assessments WHERE tenant_id = $1", tenant_id)
            return counts

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
    dsn = (
        settings.store_dsn.get_secret_value()
        if hasattr(settings.store_dsn, "get_secret_value")
        else str(settings.store_dsn)
    )
    return PostgreSQLStore(
        dsn=dsn,
        pool_min=settings.store_pool_min,
        pool_max=settings.store_pool_max,
        processed_item_ttl_hours=settings.processed_item_ttl_hours,
    )
