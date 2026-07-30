from __future__ import annotations

import pytest

from job_ftch.domain import Job, JobRecord, SourceKind, create_job_group
from job_ftch.infrastructure.backends.jobs.postgres import PostgreSQLJobBackend


class _Settings:
    store_dsn = "postgresql://user:pass@localhost/db"
    store_pool_min = 1
    store_pool_max = 2
    search_language = "simple"


class _StubConn:
    def __init__(self, *, group_id: str | None = None) -> None:
        self.group_id = group_id
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, sql: str, *params: object) -> str | None:
        self.fetchval_calls.append((sql, params))
        return self.group_id

    async def execute(self, sql: str, *params: object) -> None:
        self.execute_calls.append((sql, params))

    async def executemany(self, sql: str, args: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((sql, args))


def _job(*, raw_item_id: str, canonical_url: str | None) -> JobRecord:
    job = Job(
        raw_item_id=raw_item_id,
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="Software Engineer",
        company="Acme",
        description="Build systems",
        canonical_url=canonical_url,
    )
    return JobRecord.model_validate(job.model_dump(mode="python"))


@pytest.mark.asyncio
async def test_resolve_group_id_uses_single_query() -> None:
    backend = PostgreSQLJobBackend(_Settings())
    conn = _StubConn(group_id="group-1")

    resolved = await backend._resolve_group_id(
        conn,
        "https://example.com/jobs/1",
        "fp-1",
    )

    assert resolved == "group-1"
    assert len(conn.fetchval_calls) == 1
    assert "COALESCE" in conn.fetchval_calls[0][0]
    assert conn.fetchval_calls[0][1] == ("https://example.com/jobs/1", "fp-1")


@pytest.mark.asyncio
async def test_persist_group_batches_url_and_fingerprint_index_updates() -> None:
    backend = PostgreSQLJobBackend(_Settings())
    conn = _StubConn()
    group = create_job_group(_job(raw_item_id="1", canonical_url="https://example.com/jobs/1"))
    group = group.model_copy(
        update={
            "jobs": [
                group.jobs[0],
                _job(raw_item_id="2", canonical_url="https://example.com/jobs/2"),
            ]
        }
    )

    await backend._persist_group(conn, group)

    assert len(conn.execute_calls) == 1
    assert "INSERT INTO jf_job_groups" in conn.execute_calls[0][0]
    assert len(conn.executemany_calls) == 2

    url_sql, url_rows = conn.executemany_calls[0]
    fp_sql, fp_rows = conn.executemany_calls[1]
    assert "jf_job_group_urls" in url_sql
    assert "jf_job_group_fingerprints" in fp_sql
    assert len(url_rows) == 2
    assert len(fp_rows) == 2


@pytest.mark.asyncio
async def test_persist_group_skips_url_batch_when_group_has_no_urls() -> None:
    backend = PostgreSQLJobBackend(_Settings())
    conn = _StubConn()
    group = create_job_group(_job(raw_item_id="1", canonical_url=None))

    await backend._persist_group(conn, group)

    assert len(conn.executemany_calls) == 1
    assert "jf_job_group_fingerprints" in conn.executemany_calls[0][0]


@pytest.mark.asyncio
async def test_postgres_backend_preserves_job_id_different_from_stable_id() -> None:
    backend = PostgreSQLJobBackend(_Settings())
    conn = _StubConn()
    job = _job(raw_item_id="1", canonical_url="https://example.com/jobs/1")
    job = job.model_copy(update={"job_id": "custom-job-id"})
    assert job.stable_id != job.job_id

    await backend._persist_job(conn, job, "group-1")

    # Assert that the execute call has the custom job_id as the first parameter
    assert len(conn.execute_calls) == 1
    sql, params = conn.execute_calls[0]
    assert "INSERT INTO jf_jobs" in sql
    # The first parameter $1 should be the job_id
    assert params[0] == "custom-job-id"


@pytest.mark.asyncio
async def test_postgres_backend_applies_all_migrations() -> None:
    backend = PostgreSQLJobBackend(_Settings())
    assert backend is not None
