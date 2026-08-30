from __future__ import annotations

import pytest

from job_ftch.infrastructure.stores.postgres import PostgreSQLStore


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.fetchvals: list[tuple[str, tuple[object, ...]]] = []
        self.executes: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchval(self, sql: str, *params: object) -> int:
        self.fetchvals.append((sql, params))
        return 1

    async def execute(self, sql: str, *params: object) -> None:
        self.executes.append((sql, params))


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


@pytest.mark.asyncio
async def test_postgres_clear_run_artifacts_removes_processed_dedup_and_ledgers() -> None:
    conn = _FakeConnection()
    store = PostgreSQLStore("postgresql://example")
    store._pool = _FakePool(conn)  # noqa: SLF001 - isolate SQL contract without a live DSN.

    counts = await store.clear_run_artifacts("ai_jobs:", "ai_jobs")

    assert counts == {
        "kv": 1,
        "sets": 1,
        "observations": 1,
        "snapshots": 1,
        "source_ingest_states": 1,
        "dedup_claims": 1,
        "outbox": 1,
        "source_assessments": 1,
    }
    set_count_params = conn.fetchvals[1][1][0]
    assert "ai_jobs:processed%" in set_count_params
    assert "ai_jobs:dedup_keys%" in set_count_params
    assert "ai_jobs:dup_records%" in set_count_params
    kv_count_params = conn.fetchvals[0][1][0]
    assert "ai_jobs:bot_publish:%" in kv_count_params
    assert "ai_jobs:bot_scheduler:last_publish%" in kv_count_params
    assert "ai_jobs:bot_scheduler:pending_publish_since" in kv_count_params

    executed_sql = "\n".join(sql for sql, _params in conn.executes)
    assert "DELETE FROM jf_set WHERE key LIKE ANY" in executed_sql
    assert "DELETE FROM jf_observations WHERE tenant_id = $1" in executed_sql
    assert "DELETE FROM jf_source_snapshots WHERE tenant_id = $1" in executed_sql
    assert "DELETE FROM jf_source_ingest_state WHERE tenant_id = $1" in executed_sql
    assert "DELETE FROM jf_dedup_claims WHERE claim_key LIKE $1" in executed_sql
    assert "DELETE FROM jf_outbox WHERE tenant_id = $1" in executed_sql
    assert "DELETE FROM jf_source_assessments WHERE tenant_id = $1" in executed_sql
    assert any(
        sql == "SELECT COUNT(*) FROM jf_outbox WHERE tenant_id = $1" and params == ("ai_jobs",)
        for sql, params in conn.fetchvals
    )
    assert any(
        sql == "SELECT COUNT(*) FROM jf_source_assessments WHERE tenant_id = $1"
        and params == ("ai_jobs",)
        for sql, params in conn.fetchvals
    )
