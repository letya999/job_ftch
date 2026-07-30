"""Offline SQL-contract tests for the PGVector backend."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.backends.vector.pgvector import PgVectorBackend


class _Connection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str) -> None:
        self.executed.append(query)

    async def executemany(self, query: str, rows: list[tuple[object, ...]]) -> None:
        self.many.append((query, rows))

    async def fetch(self, query: str, *args: object) -> list[dict[str, str]]:
        self.fetches.append((query, args))
        return [{"job_id": "job-1"}, {"job_id": "job-2"}]

    async def fetchrow(self, _: str) -> tuple[int]:
        return (2,)


class _Acquire:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Connection:
        return self.conn

    async def __aexit__(self, *_: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


@pytest.fixture
def backend() -> tuple[PgVectorBackend, _Connection]:
    conn = _Connection()
    instance = PgVectorBackend.__new__(PgVectorBackend)
    instance._pool = _Pool(conn)
    instance._dimensions = 3
    instance._schema_initialized = True
    instance._logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    return instance, conn


@pytest.mark.asyncio
async def test_pgvector_upsert_serializes_rows_and_validates_dimension(backend) -> None:
    instance, conn = backend

    await instance.upsert_many(
        [
            ("job-1", [0.1, 0.2, 0.3], {"group_id": "g1", "title": "Engineer"}),
            ("job-2", [0.4, 0.5, 0.6], {}),
        ]
    )

    _, rows = conn.many[0]
    assert rows == [
        ("job-1", "g1", "[0.1,0.2,0.3]", '{"group_id": "g1", "title": "Engineer"}'),
        ("job-2", "", "[0.4,0.5,0.6]", "{}"),
    ]
    await instance.upsert("job-3", [1.0, 2.0, 3.0], {})
    with pytest.raises(ValueError, match="inside batch"):
        await instance.upsert_many([("a", [1.0], {}), ("b", [1.0, 2.0], {})])
    with pytest.raises(ValueError, match="Expected 3"):
        await instance.upsert_many([("bad", [1.0, 2.0], {})])
    await instance.upsert_many([])
    assert len(conn.many) == 2


@pytest.mark.asyncio
async def test_pgvector_search_binds_allowed_filters_and_rejects_unknown_keys(backend) -> None:
    instance, conn = backend

    assert await instance.search([0.1, 0.2, 0.3], limit=5, filter={"company": "Acme"}) == [
        "job-1",
        "job-2",
    ]
    query, args = conn.fetches[0]
    assert "payload->>'company' = $3" in query
    assert args == ("[0.1,0.2,0.3]", 5, "Acme")
    with pytest.raises(ValueError, match="not allowed"):
        await instance.search([0.1, 0.2, 0.3], limit=1, filter={"company; DROP": "x"})
    with pytest.raises(ValueError, match="Expected 3"):
        await instance.search([0.1], limit=1)


@pytest.mark.asyncio
async def test_pgvector_initializes_schema_and_clear_keeps_table(backend) -> None:
    instance, conn = backend
    instance._schema_initialized = False

    await instance._init_schema(3)

    assert instance._schema_initialized is True
    assert "CREATE EXTENSION IF NOT EXISTS vector" in conn.executed[0]
    assert "VECTOR(3)" in conn.executed[1]
    assert "jf_job_vectors_group_idx" in conn.executed[2]
    assert await instance.clear() == 2
    assert conn.executed[-1] == "DELETE FROM jf_job_vectors;"
