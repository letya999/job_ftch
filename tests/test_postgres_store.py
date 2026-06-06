from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from psycopg.types.json import Jsonb

from domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RememberedDedupKey,
    SourceKind,
)
from infrastructure.stores.postgres import PostgresStore


@dataclass
class FakeDatabase:
    processed_raw_items: dict[str, dict[str, object]] = field(default_factory=dict)
    dedup_keys: dict[str, dict[str, object]] = field(default_factory=dict)
    source_cursors: dict[str, str] = field(default_factory=dict)
    duplicate_records: list[dict[str, object]] = field(default_factory=list)
    run_summaries: dict[str, dict[str, object]] = field(default_factory=dict)
    rejections: dict[str, dict[str, object]] = field(default_factory=dict)


class FakeCursor:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self.rowcount = 0
        self._row: tuple[object, ...] | None = None
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> None:
        normalized = " ".join(query.lower().split())
        params = params or ()
        self.rowcount = 0
        self._row = None
        self._rows = []

        if normalized.startswith("create table"):
            return
        if normalized.startswith("select 1 from processed_raw_items"):
            self._row = (1,) if str(params[0]) in self._database.processed_raw_items else None
            return
        if normalized.startswith("insert into processed_raw_items"):
            stable_id = str(params[0])
            if stable_id not in self._database.processed_raw_items:
                self._database.processed_raw_items[stable_id] = {
                    "source_kind": params[1],
                    "source_name": params[2],
                    "external_id": params[3],
                    "url": params[4],
                    "first_seen_at": params[5],
                    "last_seen_at": params[6],
                }
                self.rowcount = 1
            return
        if normalized.startswith("update processed_raw_items"):
            stable_id = str(params[1])
            self._database.processed_raw_items[stable_id]["last_seen_at"] = params[0]
            self.rowcount = 1
            return
        if normalized.startswith("select 1 from dedup_keys"):
            self._row = (1,) if str(params[0]) in self._database.dedup_keys else None
            return
        if normalized.startswith("insert into dedup_keys"):
            key = str(params[0])
            if key not in self._database.dedup_keys:
                self._database.dedup_keys[key] = {
                    "kind": params[1],
                    "item_id": params[2],
                    "source_kind": params[3],
                    "source_name": params[4],
                    "match_text": params[5],
                    "url": params[6],
                    "created_at": params[7],
                }
                self.rowcount = 1
            return
        if normalized.startswith("select dedup_key, kind, item_id"):
            rows = [
                (
                    key,
                    payload["kind"],
                    payload["item_id"],
                    payload["source_kind"],
                    payload["source_name"],
                    payload["match_text"],
                    payload["url"],
                )
                for key, payload in self._database.dedup_keys.items()
            ]
            if "where kind = %s" in normalized:
                rows = [row for row in rows if row[1] == params[0]]
            self._rows = rows
            return
        if normalized.startswith("insert into duplicate_records"):
            self._database.duplicate_records.append(
                {
                    "item_id": params[0],
                    "reason": params[1],
                    "payload_json": _unwrap_jsonb(params[2]),
                    "created_at": params[3],
                }
            )
            self.rowcount = 1
            return
        if normalized.startswith("select payload_json from duplicate_records"):
            self._rows = [
                (payload["payload_json"],) for payload in self._database.duplicate_records
            ]
            return
        if normalized.startswith("select cursor_value from source_cursors"):
            cursor_value = self._database.source_cursors.get(str(params[0]))
            self._row = (cursor_value,) if cursor_value is not None else None
            return
        if normalized.startswith("insert into source_cursors"):
            self._database.source_cursors[str(params[0])] = str(params[1])
            self.rowcount = 1
            return
        if normalized.startswith("insert into run_summaries"):
            self._database.run_summaries[str(params[0])] = {
                "started_at": params[1],
                "finished_at": params[2],
                "payload_json": _unwrap_jsonb(params[3]),
            }
            self.rowcount = 1
            return
        if normalized.startswith("insert into rejections"):
            self._database.rejections[str(params[0])] = {
                "run_id": params[1],
                "stage": params[2],
                "reason": params[3],
                "payload_json": _unwrap_jsonb(params[4]),
                "created_at": params[5],
            }
            self.rowcount = 1
            return

        raise AssertionError(f"Unexpected SQL: {query}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._database)


def _store(database: FakeDatabase) -> PostgresStore:
    return PostgresStore(
        "postgresql://job_ftch:job_ftch@localhost:5432/job_ftch",
        connection_factory=lambda: FakeConnection(database),
    )


def _unwrap_jsonb(value: object) -> dict[str, object]:
    assert isinstance(value, Jsonb)
    assert isinstance(value.obj, dict)
    return value.obj


@pytest.mark.asyncio
async def test_postgres_store_processed_ids_persist_across_connections() -> None:
    database = FakeDatabase()
    store = _store(database)

    assert await store.try_mark_processed("raw-1") is True
    assert await store.try_mark_processed("raw-1") is False
    assert await store.has_processed("raw-1") is True

    reopened = _store(database)

    assert await reopened.has_processed("raw-1") is True


@pytest.mark.asyncio
async def test_postgres_store_dedup_keys_are_atomic() -> None:
    store = _store(FakeDatabase())
    first = RememberedDedupKey(
        key="canonical-url:https://example.com/job",
        kind=DedupKeyKind.URL,
        item_id="job-1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="example",
        url="https://example.com/job",
    )
    duplicate = first.model_copy(update={"item_id": "job-2"})

    assert await store.try_remember_dedup_key(first)
    assert not await store.try_remember_dedup_key(duplicate)
    assert await store.has_dedup_key("canonical-url:https://example.com/job")
    assert await store.list_dedup_keys(DedupKeyKind.URL.value) == (first,)


@pytest.mark.asyncio
async def test_postgres_store_persists_duplicate_records() -> None:
    database = FakeDatabase()
    store = _store(database)
    duplicate = DuplicateRecord(
        item_id="raw-2",
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        reason=DuplicateRejectionReason.DUPLICATE_CONTENT,
        duplicate_key="content:abc",
        matched_key="content:abc",
        matched_item_id="raw-1",
        matched_source_kind=SourceKind.DEBUG,
        matched_source_name="fixture",
        details="Same normalized content.",
    )

    await store.record_duplicate(duplicate)

    assert await store.list_duplicate_records() == (duplicate,)


@pytest.mark.asyncio
async def test_postgres_store_source_cursor_persists_across_connections() -> None:
    database = FakeDatabase()
    store = _store(database)

    await store.set_source_cursor("telegram_channel:ai_jobs", "42")
    await store.set_run_state("career_site:greenhouse", "page-2")

    reopened = _store(database)

    assert await reopened.get_source_cursor("telegram_channel:ai_jobs") == "42"
    assert await reopened.get_run_state("career_site:greenhouse") == "page-2"


@pytest.mark.asyncio
async def test_postgres_store_persists_summary_and_rejection_payloads() -> None:
    database = FakeDatabase()
    store = _store(database)

    await store.save_run_summary(
        "run-1",
        {
            "run_id": "run-1",
            "started_at": "2026-06-06T00:00:00+00:00",
            "finished_at": "2026-06-06T00:01:00+00:00",
            "emitted": 1,
        },
    )
    await store.save_rejection(
        "rej-1",
        run_id="run-1",
        stage="origin_policy",
        reason="disallowed_url_host",
        payload={"source_name": "fixture", "text_preview": "Hiring"},
    )

    assert database.run_summaries["run-1"]["payload_json"]["emitted"] == 1
    assert database.rejections["rej-1"]["payload_json"]["text_preview"] == "Hiring"
