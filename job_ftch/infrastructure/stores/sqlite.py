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
    _SQL_SET_CLEAR = "DELETE FROM jf_set WHERE key = ?"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = ? AND member = ?"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = ?"
    _SQL_OUTBOX_ENQUEUE = "INSERT OR IGNORE INTO jf_outbox (outbox_id, tenant_id, idempotency_key, state, payload_json) VALUES (?, ?, ?, ?, ?)"
    _SQL_OUTBOX_GET = "SELECT payload_json, state FROM jf_outbox WHERE idempotency_key = ?"
    _SQL_OUTBOX_PENDING = "SELECT payload_json, state FROM jf_outbox WHERE state = ? AND tenant_id = ? ORDER BY updated_at ASC LIMIT ?"
    _SQL_OUTBOX_DELIVERED = "UPDATE jf_outbox SET state = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE idempotency_key = ?"
    _SQL_DEDUP_CLAIM_ACQUIRE = """
        INSERT INTO jf_dedup_claims (claim_key, owner_id, expires_at)
        VALUES (?, ?, datetime('now', '+' || ? || ' seconds'))
        ON CONFLICT(claim_key) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at
        WHERE jf_dedup_claims.expires_at <= datetime('now')
    """
    _SQL_DEDUP_CLAIM_OWNER = "SELECT owner_id FROM jf_dedup_claims WHERE claim_key = ?"
    _SQL_DEDUP_CLAIM_RELEASE = "DELETE FROM jf_dedup_claims WHERE claim_key = ? AND owner_id = ?"
    _SQL_OBSERVATION_GET = "SELECT payload_json FROM jf_observations WHERE tenant_id = ? AND stable_id = ? AND content_hash = ?"
    _SQL_OBSERVATION_MAX_VERSION = (
        "SELECT MAX(content_version) FROM jf_observations WHERE tenant_id = ? AND stable_id = ?"
    )
    _SQL_OBSERVATION_INSERT = "INSERT INTO jf_observations (tenant_id, stable_id, content_hash, content_version, payload_json) VALUES (?, ?, ?, ?, ?)"

    # ADR-031: source snapshot table SQL
    _SQL_SNAPSHOT_LAST_RUN_IDS = """
        SELECT stable_id FROM jf_source_snapshots
        WHERE tenant_id = ? AND source_id = ?
          AND run_id = (
              SELECT run_id FROM jf_source_snapshots
              WHERE tenant_id = ? AND source_id = ?
              ORDER BY run_at DESC LIMIT 1
          )
    """
    _SQL_SNAPSHOT_LAST_RUN_HASHES = """
        SELECT stable_id, item_hash FROM jf_source_snapshots
        WHERE tenant_id = ? AND source_id = ? AND run_id = (
            SELECT run_id FROM jf_source_snapshots
            WHERE tenant_id = ? AND source_id = ? ORDER BY run_at DESC LIMIT 1
        )
    """
    _SQL_SNAPSHOT_INSERT = """
        INSERT INTO jf_source_snapshots
            (tenant_id, source_id, run_id, stable_id, item_hash, item_json, run_at)
        VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    """
    _SQL_SNAPSHOT_PURGE = """
        DELETE FROM jf_source_snapshots
        WHERE tenant_id = ? AND source_id = ?
          AND run_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', printf('-%d days', ?))
    """
    _SQL_SNAPSHOT_PURGE_COUNT = "SELECT changes()"
    _SQL_SOURCE_ASSESSMENT_GET = """
        SELECT payload_json FROM jf_source_assessments
        WHERE tenant_id = ? AND source_id = ?
    """
    _SQL_SOURCE_ASSESSMENT_UPSERT = """
        INSERT INTO jf_source_assessments
            (tenant_id, source_id, source_type, schema_version, assessed_at, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(tenant_id, source_id) DO UPDATE SET
            source_type=excluded.source_type,
            schema_version=excluded.schema_version,
            assessed_at=excluded.assessed_at,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
    """
    _SQL_SOURCE_INGEST_STATE_GET = """
        SELECT payload_json FROM jf_source_ingest_state
        WHERE tenant_id = ? AND source_id = ?
    """
    _SQL_SOURCE_INGEST_STATE_UPSERT = """
        INSERT INTO jf_source_ingest_state
            (tenant_id, source_id, bootstrap_completed_at, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, source_id) DO UPDATE SET
            bootstrap_completed_at=excluded.bootstrap_completed_at,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
    """
    _SQL_OPERATOR_FLAG_GET = """
        SELECT source_key, important, set_by, set_at, note
        FROM jf_source_operator_flags
        WHERE tenant_id = ? AND source_key = ?
    """
    _SQL_OPERATOR_FLAG_UPSERT = """
        INSERT INTO jf_source_operator_flags
            (tenant_id, source_key, important, set_by, set_at, note)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, source_key) DO UPDATE SET
            important=excluded.important,
            set_by=excluded.set_by,
            set_at=excluded.set_at,
            note=excluded.note
    """
    _SQL_OPERATOR_FLAG_LIST = """
        SELECT source_key, important, set_by, set_at, note
        FROM jf_source_operator_flags
        WHERE tenant_id = ?
        ORDER BY source_key
    """
    _SQL_PIPELINE_RUN_STATS_UPSERT = """
        INSERT INTO jf_pipeline_run_stats (
            tenant_id, source_run_id, started_at, finished_at, duration_ms,
            source_count, ok_sources, fail_sources, fetched, extracted, emitted,
            review, rejected, dropped, failed, duplicates, llm_calls, llm_tokens_in,
            llm_tokens_out, llm_latency_ms, llm_cost_usd, conversion_extract,
            conversion_accept, extra_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, source_run_id) DO UPDATE SET
            started_at=excluded.started_at,
            finished_at=excluded.finished_at,
            duration_ms=excluded.duration_ms,
            source_count=excluded.source_count,
            ok_sources=excluded.ok_sources,
            fail_sources=excluded.fail_sources,
            fetched=excluded.fetched,
            extracted=excluded.extracted,
            emitted=excluded.emitted,
            review=excluded.review,
            rejected=excluded.rejected,
            dropped=excluded.dropped,
            failed=excluded.failed,
            duplicates=excluded.duplicates,
            llm_calls=excluded.llm_calls,
            llm_tokens_in=excluded.llm_tokens_in,
            llm_tokens_out=excluded.llm_tokens_out,
            llm_latency_ms=excluded.llm_latency_ms,
            llm_cost_usd=excluded.llm_cost_usd,
            conversion_extract=excluded.conversion_extract,
            conversion_accept=excluded.conversion_accept,
            extra_json=excluded.extra_json
    """
    _SQL_SOURCE_RUN_STATS_UPSERT = """
        INSERT INTO jf_source_run_stats (
            tenant_id, source_run_id, source_id, source_key, source_kind, source_name,
            status, started_at, finished_at, yielded, fetched, extracted, emitted,
            dropped, failed, duration_ms, llm_latency_ms, llm_cost_usd, conversion_accept,
            quality_reliable, quality_rich, quality_high_relevance, quality_important, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, source_run_id, source_id) DO UPDATE SET
            source_key=excluded.source_key,
            source_kind=excluded.source_kind,
            source_name=excluded.source_name,
            status=excluded.status,
            started_at=excluded.started_at,
            finished_at=excluded.finished_at,
            yielded=excluded.yielded,
            fetched=excluded.fetched,
            extracted=excluded.extracted,
            emitted=excluded.emitted,
            dropped=excluded.dropped,
            failed=excluded.failed,
            duration_ms=excluded.duration_ms,
            llm_latency_ms=excluded.llm_latency_ms,
            llm_cost_usd=excluded.llm_cost_usd,
            conversion_accept=excluded.conversion_accept,
            quality_reliable=excluded.quality_reliable,
            quality_rich=excluded.quality_rich,
            quality_high_relevance=excluded.quality_high_relevance,
            quality_important=excluded.quality_important,
            error=excluded.error
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        processed_item_ttl_hours: int | None = 24,
    ) -> None:
        super().__init__(processed_item_ttl_hours=processed_item_ttl_hours)
        self._path = str(path)
        self._conn: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if aiosqlite is None:
            raise ImportError("aiosqlite is required for SQLiteStore. Install with [sqlite] extra.")

        async with self._init_lock:
            if self._conn is None:
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = await aiosqlite.connect(self._path)
                self._conn.row_factory = aiosqlite.Row
                await self._initialize()
        return self._conn

    async def _initialize(self) -> None:
        migrations_dir = Path(__file__).parent / "migrations"
        for name in (
            "001_initial_schema.sql",
            "002_source_snapshots.sql",
            "003_ontology.sql",
            "004_source_assessment.sql",
            "005_observation_ledger.sql",
            "006_dedup_claims.sql",
            "007_outbox.sql",
            "009_ontology_occurrences.sql",
            "011_ontology_graph.sql",
            "012_ontology_term_stats.sql",
            "013_compiled_ontology.sql",
            "014_run_stats.sql",
        ):
            path = migrations_dir / name
            if not path.exists():
                continue
            # executescript() issues an implicit COMMIT before running, so no separate
            # commit is needed. Only call this during initialization (no pending txns).
            await self._conn.executescript(path.read_text())  # type: ignore[union-attr]
        async with self._conn.execute("PRAGMA table_info(jf_outbox)") as cursor:  # type: ignore[union-attr]
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if "tenant_id" not in columns:
            await self._conn.execute(
                "ALTER TABLE jf_outbox ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jf_outbox_tenant_state ON jf_outbox(tenant_id, state)"
        )
        await self._conn.commit()

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        conn = await self._ensure_initialized()
        await conn.execute(sql, params)
        await conn.commit()

    async def _execute_batch(self, sql: str, params_list: tuple[tuple[object, ...], ...]) -> None:
        conn = await self._ensure_initialized()
        try:
            await conn.execute("BEGIN TRANSACTION")
            await conn.executemany(sql, params_list)
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

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

    async def reset_namespace(self, prefix: str) -> None:
        conn = await self._ensure_initialized()
        await conn.execute("DELETE FROM jf_kv WHERE key LIKE ?", (f"{prefix}%",))
        await conn.execute("DELETE FROM jf_set WHERE key LIKE ?", (f"{prefix}%",))
        await conn.commit()

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
        conn = await self._ensure_initialized()

        async def _count(sql: str, params: tuple[object, ...] = ()) -> int:
            async with conn.execute(sql, params) as cursor:
                row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0

        kv_where = " OR ".join("key LIKE ?" for _ in kv_patterns)
        set_where = " OR ".join("key LIKE ?" for _ in set_patterns)
        # Clauses contain only a fixed number of ``key LIKE ?`` fragments.
        kv_count_sql = "SELECT COUNT(*) FROM jf_kv WHERE " + kv_where  # nosec B608
        set_count_sql = "SELECT COUNT(*) FROM jf_set WHERE " + set_where  # nosec B608
        counts = {
            "kv": await _count(kv_count_sql, kv_patterns),
            "sets": await _count(set_count_sql, set_patterns),
            "observations": await _count(
                "SELECT COUNT(*) FROM jf_observations WHERE tenant_id = ?", (tenant_id,)
            ),
            "snapshots": await _count(
                "SELECT COUNT(*) FROM jf_source_snapshots WHERE tenant_id = ?", (tenant_id,)
            ),
            "source_ingest_states": await _count(
                "SELECT COUNT(*) FROM jf_source_ingest_state WHERE tenant_id = ?", (tenant_id,)
            ),
            "dedup_claims": await _count(
                "SELECT COUNT(*) FROM jf_dedup_claims WHERE claim_key LIKE ?", (f"{prefix}%",)
            ),
            "outbox": await _count(
                "SELECT COUNT(*) FROM jf_outbox WHERE tenant_id = ?", (tenant_id,)
            ),
            "source_assessments": await _count(
                "SELECT COUNT(*) FROM jf_source_assessments WHERE tenant_id = ?", (tenant_id,)
            ),
        }
        await conn.execute("DELETE FROM jf_kv WHERE " + kv_where, kv_patterns)  # nosec B608
        await conn.execute("DELETE FROM jf_set WHERE " + set_where, set_patterns)  # nosec B608
        await conn.execute("DELETE FROM jf_observations WHERE tenant_id = ?", (tenant_id,))
        await conn.execute("DELETE FROM jf_source_snapshots WHERE tenant_id = ?", (tenant_id,))
        await conn.execute("DELETE FROM jf_source_ingest_state WHERE tenant_id = ?", (tenant_id,))
        await conn.execute("DELETE FROM jf_dedup_claims WHERE claim_key LIKE ?", (f"{prefix}%",))
        await conn.execute("DELETE FROM jf_outbox WHERE tenant_id = ?", (tenant_id,))
        await conn.execute("DELETE FROM jf_source_assessments WHERE tenant_id = ?", (tenant_id,))
        await conn.commit()
        return counts

    async def ping(self) -> bool:
        """Check connection health."""
        try:
            await self._fetchone("SELECT 1")
            return True
        except Exception:
            return False


@register_store("sqlite")
def _build_sqlite_store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(
        path=settings.store_path,
        processed_item_ttl_hours=settings.processed_item_ttl_hours,
    )
