"""PostgreSQL-backed persistent store for production idempotency."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb

from application.registry import register_store
from domain import DuplicateRecord, RememberedDedupKey

if TYPE_CHECKING:
    from config import Settings

ConnectionFactory = Callable[[], Any]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _jsonb_payload(payload: Mapping[str, object]) -> Jsonb:
    return Jsonb(dict(payload), dumps=_json_dumps)


class PostgresStore:
    def __init__(
        self,
        dsn: str,
        *,
        connection_factory: ConnectionFactory | None = None,
        initialize: bool = True,
    ) -> None:
        self._dsn = dsn
        self._connection_factory = connection_factory or self._connect
        if initialize:
            self._initialize()

    def _connect(self) -> Any:
        return psycopg.connect(self._dsn)

    def _initialize(self) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS processed_raw_items (
                        stable_id TEXT PRIMARY KEY,
                        source_kind TEXT,
                        source_name TEXT,
                        external_id TEXT,
                        url TEXT,
                        first_seen_at TIMESTAMPTZ NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS dedup_keys (
                        dedup_key TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        source_kind TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        match_text TEXT,
                        url TEXT,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS duplicate_records (
                        id BIGSERIAL PRIMARY KEY,
                        item_id TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS source_cursors (
                        source_key TEXT PRIMARY KEY,
                        cursor_value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS jobs (
                        stable_id TEXT PRIMARY KEY,
                        raw_item_id TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        quality_score DOUBLE PRECISION,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS run_summaries (
                        run_id TEXT PRIMARY KEY,
                        started_at TEXT,
                        finished_at TEXT,
                        payload_json JSONB NOT NULL
                    )
                    """
            )
            cursor.execute(
                """
                    CREATE TABLE IF NOT EXISTS rejections (
                        id TEXT PRIMARY KEY,
                        run_id TEXT,
                        stage TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
            )

    async def has_processed(self, item_id: str) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM processed_raw_items WHERE stable_id = %s",
                (item_id,),
            )
            row = cursor.fetchone()
        return row is not None

    async def mark_processed(self, item_id: str) -> None:
        await self.try_mark_processed(item_id)

    async def try_mark_processed(
        self,
        item_id: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
        external_id: str | None = None,
        url: str | None = None,
    ) -> bool:
        now = _now_iso()
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO processed_raw_items (
                    stable_id, source_kind, source_name, external_id, url, first_seen_at, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (stable_id) DO NOTHING
                """,
                (item_id, source_kind, source_name, external_id, url, now, now),
            )
            inserted = bool(cursor.rowcount == 1)
            if not inserted:
                cursor.execute(
                    """
                    UPDATE processed_raw_items
                    SET last_seen_at = %s
                    WHERE stable_id = %s
                    """,
                    (now, item_id),
                )
        return inserted

    async def has_dedup_key(self, key: str) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM dedup_keys WHERE dedup_key = %s", (key,))
            row = cursor.fetchone()
        return row is not None

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        await self.try_remember_dedup_key(record)

    async def try_remember_dedup_key(self, record: RememberedDedupKey) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO dedup_keys (
                        dedup_key, kind, item_id, source_kind, source_name, match_text, url,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedup_key) DO NOTHING
                    """,
                (
                    record.key,
                    record.kind.value,
                    record.item_id,
                    record.source_kind.value,
                    record.source_name,
                    record.match_text,
                    record.url,
                    _now_iso(),
                ),
            )
            return bool(cursor.rowcount == 1)

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        query = """
            SELECT dedup_key, kind, item_id, source_kind, source_name, match_text, url
            FROM dedup_keys
        """
        params: tuple[object, ...] = ()
        if kind is not None:
            query += " WHERE kind = %s"
            params = (kind,)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return tuple(
            RememberedDedupKey.model_validate(
                {
                    "key": row[0],
                    "kind": row[1],
                    "item_id": row[2],
                    "source_kind": row[3],
                    "source_name": row[4],
                    "match_text": row[5],
                    "url": row[6],
                }
            )
            for row in rows
        )

    async def record_duplicate(self, record: DuplicateRecord) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO duplicate_records (item_id, reason, payload_json, created_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                (
                    record.item_id,
                    record.reason.value,
                    _jsonb_payload(record.model_dump(mode="json")),
                    _now_iso(),
                ),
            )

    async def list_duplicate_records(self) -> tuple[DuplicateRecord, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT payload_json
                    FROM duplicate_records
                    ORDER BY id ASC
                    """,
            )
            rows = cursor.fetchall()
        return tuple(DuplicateRecord.model_validate(row[0]) for row in rows)

    async def get_run_state(self, key: str) -> str | None:
        return await self.get_source_cursor(key)

    async def set_run_state(self, key: str, value: str) -> None:
        await self.set_source_cursor(key, value)

    async def get_source_cursor(self, source_key: str) -> str | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT cursor_value FROM source_cursors WHERE source_key = %s",
                (source_key,),
            )
            row = cursor.fetchone()
        return str(row[0]) if row is not None else None

    async def set_source_cursor(self, source_key: str, cursor_value: str) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO source_cursors (source_key, cursor_value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source_key) DO UPDATE SET
                        cursor_value = excluded.cursor_value,
                        updated_at = excluded.updated_at
                    """,
                (source_key, cursor_value, _now_iso()),
            )

    async def save_run_summary(self, run_id: str, payload: Mapping[str, object]) -> None:
        started_at = _optional_string(payload.get("started_at"))
        finished_at = _optional_string(payload.get("finished_at"))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO run_summaries (run_id, started_at, finished_at, payload_json)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at,
                        payload_json = excluded.payload_json
                    """,
                (run_id, started_at, finished_at, _jsonb_payload(payload)),
            )

    async def save_rejection(
        self,
        rejection_id: str,
        *,
        run_id: str | None,
        stage: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO rejections (id, run_id, stage, reason, payload_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        run_id = excluded.run_id,
                        stage = excluded.stage,
                        reason = excluded.reason,
                        payload_json = excluded.payload_json
                    """,
                (rejection_id, run_id, stage, reason, _jsonb_payload(payload), _now_iso()),
            )

    async def save_job(
        self,
        stable_id: str,
        *,
        raw_item_id: str,
        payload: Mapping[str, object],
        quality_score: float | None = None,
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (stable_id, raw_item_id, payload_json, quality_score, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (stable_id) DO UPDATE SET
                    raw_item_id = excluded.raw_item_id,
                    payload_json = excluded.payload_json,
                    quality_score = excluded.quality_score
                """,
                (
                    stable_id,
                    raw_item_id,
                    _jsonb_payload(payload),
                    quality_score,
                    _now_iso(),
                ),
            )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@register_store("postgres")
def _build_postgres_store(settings: Settings) -> PostgresStore:
    if settings.postgres_dsn is None:
        msg = "PostgreSQL store backend requires JOB_FTCH_POSTGRES_DSN."
        raise ValueError(msg)
    return PostgresStore(settings.postgres_dsn)
