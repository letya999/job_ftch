"""Tests for PostgresOntologyStore.

These tests cover the postgres backend of the live ontology store
that the pipeline builder reads from to enrich the user's profile
with skills / roles / anti-patterns the LLM has extracted from
their shots.

Why this exists: the previous implementation routed
``store_backend="postgres"`` through a SQLite-only
``DBOntologyStore`` factory that opened a (non-existent) file path
and silently failed in ``create_ontology_store``'s
``try/except Exception``. The result was an empty ontology even
after the user added a hundred shots.

The tests below hit a real Postgres via testcontainers, or fall
back to a skipped-suite contract check when no database is
available. The unit tests (no DB) cover the SQL composition and
the in-memory contract, so a wrong SQL string is caught even
without a live database.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# SQL-level unit tests: build the SQL with the right shape, replace
# asyncpg with a stub, and verify the calls go through.
# ---------------------------------------------------------------------------


class _StubAcquire:
    def __init__(self, conn: _StubConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _StubConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _StubConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_rows: list[tuple[object, ...]] = []
        self.fetchone_row: tuple[object, ...] | None = None

    async def execute(self, sql: str, *params: object) -> None:
        self.calls.append((sql, params))

    async def fetch(self, sql: str, *params: object) -> list[tuple[object, ...]]:
        self.calls.append((sql, params))
        return list(self.fetch_rows)

    async def fetchrow(self, sql: str, *params: object) -> tuple[object, ...] | None:
        self.calls.append((sql, params))
        return self.fetchone_row

    def transaction(self) -> _StubAcquire:
        return _StubAcquire(self)


class _StubPool:
    def __init__(self, conn: _StubConn) -> None:
        self._conn = conn

    def acquire(self) -> _StubAcquire:
        return _StubAcquire(self._conn)

    async def close(self) -> None:
        return None


class _StubAsyncpg:
    """Stub for ``asyncpg`` that returns a stub pool from
    ``create_pool``. ``create_pool`` is async (matches asyncpg's
    real signature).
    """

    def __init__(self, pool: _StubPool) -> None:
        self._pool = pool
        self.last_dsn: str | None = None
        self.last_min: int | None = None
        self.last_max: int | None = None
        self.create_pool_called = False

    async def create_pool(self, dsn: str, *, min_size: int, max_size: int) -> _StubPool:
        self.create_pool_called = True
        self.last_dsn = dsn
        self.last_min = min_size
        self.last_max = max_size
        return self._pool


def _make_store_with_stub_pool() -> tuple[Any, _StubConn]:
    """Build a PostgresOntologyStore with a stubbed asyncpg pool.

    The test patches ``asyncpg`` in the module namespace, so the
    store's ``_ensure_initialized`` returns our stub pool.
    """
    from job_ftch.infrastructure.ontology import postgres_store

    stub_pool = _StubPool(_StubConn())
    stub_asyncpg = _StubAsyncpg(stub_pool)
    postgres_store.asyncpg = stub_asyncpg  # type: ignore[attr-defined]
    store = postgres_store.PostgresOntologyStore(
        dsn="postgresql://u:p@localhost/db", pool_min=1, pool_max=5
    )
    return store, stub_pool._conn


# ---------------------------------------------------------------------------
# Construction: DSN and pool sizing
# ---------------------------------------------------------------------------


def test_ensure_initialized_uses_dsn_and_pool_size() -> None:
    """The store must call ``asyncpg.create_pool`` with the DSN
    and the pool min/max sizes the constructor received. The
    regression: a previous version tried to open a sqlite file
    even with store_dsn set, which silently failed in the
    tenant_runner's ``try/except``.
    """
    from job_ftch.infrastructure.ontology import postgres_store

    stub_pool = _StubPool(_StubConn())
    stub_asyncpg = _StubAsyncpg(stub_pool)
    postgres_store.asyncpg = stub_asyncpg  # type: ignore[attr-defined]
    store = postgres_store.PostgresOntologyStore(
        dsn="postgresql://u:p@localhost/db",
        pool_min=3,
        pool_max=7,
    )
    asyncio.run(store.list_skills())
    assert stub_asyncpg.create_pool_called
    assert stub_asyncpg.last_dsn == "postgresql://u:p@localhost/db"
    assert stub_asyncpg.last_min == 3
    assert stub_asyncpg.last_max == 7


def test_factory_uses_dsn_when_postgres() -> None:
    """The registry factory must produce a PostgresOntologyStore
    when ``store_backend == 'postgres'``. Previously a bug routed
    postgres through the SQLite DBOntologyStore factory and the
    store silently failed.
    """
    from job_ftch.application.registry import create_ontology_store
    from job_ftch.config import Settings
    from job_ftch.infrastructure.ontology.postgres_store import (
        PostgresOntologyStore,
    )

    settings = Settings.model_construct(
        store_backend="postgres",
        store_dsn="postgresql://x:y@localhost:5432/test",
        store_pool_min=1,
        store_pool_max=5,
    )
    store = create_ontology_store(settings)
    assert isinstance(store, PostgresOntologyStore)


def test_factory_uses_db_for_sqlite(tmp_path: Any) -> None:
    """The legacy ``store_backend='sqlite'`` path still produces
    the SQLite-backed ``DBOntologyStore``. This is the contract
    for legacy deployments and for in-memory tests.
    """
    from job_ftch.application.registry import create_ontology_store
    from job_ftch.config import Settings
    from job_ftch.infrastructure.ontology.db_store import DBOntologyStore

    path = tmp_path / "ont.db"
    settings = Settings.model_construct(
        store_backend="sqlite",
        store_path=path,
    )
    store = create_ontology_store(settings)
    assert isinstance(store, DBOntologyStore)


# ---------------------------------------------------------------------------
# SQL contract: the SQL strings must match the postgres migration
# (003_ontology_pg.sql). A typo here is silent — the store happily
# executes but no rows appear in the table.
# ---------------------------------------------------------------------------


def test_upsert_skill_uses_on_conflict() -> None:
    store, conn = _make_store_with_stub_pool()
    asyncio.run(store.upsert_skill("python", alias="Python", lang="en"))
    assert len(conn.calls) == 4
    delete_sql, delete_params = conn.calls[0]
    assert "DELETE FROM jf_ontology_skill" in delete_sql
    assert delete_params == ("python", "en", "positive")
    sql, params = conn.calls[2]
    assert "ON CONFLICT (canonical, alias, lang) DO UPDATE SET" in sql
    assert params[:3] == ("python", "Python", "en")
    assert params[3] == "positive"
    occurrence_sql, occurrence_params = conn.calls[3]
    assert "jf_ontology_occurrence" in occurrence_sql
    assert occurrence_params[:5] == ("skill", "python", "Python", "en", "positive")


def test_upsert_role_uses_canonical_as_alias_when_missing() -> None:
    """If the caller passes ``alias=None`` we must store the
    canonical as the alias so the (canonical, alias, lang) PK
    is not violated by NULL.
    """
    store, conn = _make_store_with_stub_pool()
    asyncio.run(store.upsert_role("ai engineer", alias=None, lang="en"))
    sql, params = conn.calls[2]
    assert params[:3] == ("ai engineer", "ai engineer", "en")
    assert params[3] == "positive"


def test_list_skills_lang_filters_by_lang() -> None:
    store, conn = _make_store_with_stub_pool()
    asyncio.run(store.list_skills(lang="ru"))
    sql, params = conn.calls[0]
    assert "AND lang" in sql
    assert params == ("ru",)


def test_upsert_seniority_and_anti_pattern_have_pk() -> None:
    store, conn = _make_store_with_stub_pool()
    asyncio.run(store.upsert_seniority("senior"))
    asyncio.run(store.upsert_anti_pattern("training models from scratch"))
    sql1, _ = conn.calls[0]
    sql2, _ = conn.calls[1]
    assert "ON CONFLICT (level) DO NOTHING" in sql1
    assert "ON CONFLICT (pattern) DO NOTHING" in sql2


def test_upsert_positive_and_negative_keywords_use_term_conflict_update() -> None:
    store, conn = _make_store_with_stub_pool()
    asyncio.run(store.upsert_positive_keyword("llm", weight=5))
    asyncio.run(store.upsert_negative_keyword("etl only", weight=2))
    sql1, params1 = conn.calls[1]
    sql2, params2 = conn.calls[3]
    assert "ON CONFLICT (term) DO UPDATE SET weight = EXCLUDED.weight" in sql1
    assert "ON CONFLICT (term) DO UPDATE SET weight = EXCLUDED.weight" in sql2
    assert params1 == ("llm", 5)
    assert params2 == ("etl only", 2)
    assert "DELETE FROM jf_ontology_negative_keyword" in conn.calls[0][0]
    assert "DELETE FROM jf_ontology_positive_keyword" in conn.calls[2][0]


def test_upsert_shot_graph_replaces_graph_tables() -> None:
    from job_ftch.application.ontology_graph_builder import (
        build_ontology_graph_from_compiled,
    )
    from job_ftch.domain import (
        CompiledOntology,
        CompiledOntologyTerm,
    )

    store, conn = _make_store_with_stub_pool()
    ontology = CompiledOntology(
        terms=(
            CompiledOntologyTerm(
                canonical="target role",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("shot-1",),
                accepted=True,
                confidence=0.9,
                weight=0.8,
            ),
        ),
    )
    graph = build_ontology_graph_from_compiled(
        ontology=ontology,
        graph_id="compiled:test",
        shot_id="shot-1",
        lang="en",
    )

    asyncio.run(store.upsert_shot_graph(graph))

    sql_text = "\n".join(sql for sql, _params in conn.calls)
    assert "DELETE FROM jf_ontology_graph_version" in sql_text
    assert "INSERT INTO jf_ontology_graph_version" in sql_text
    assert "INSERT INTO jf_ontology_node" in sql_text
    assert "INSERT INTO jf_ontology_edge" in sql_text
    assert "INSERT INTO jf_ontology_evidence" in sql_text


# ---------------------------------------------------------------------------
# End-to-end against a real Postgres testcontainer if available.
# Skipped automatically when the user has no docker / testcontainers.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("JOB_FTCH_TEST_PG_DSN"),
    reason="JOB_FTCH_TEST_PG_DSN not set; skipping live-DB test",
)
def test_postgres_ontology_round_trip() -> None:
    """Write a skill + role, read it back, delete it.

    This is the regression test for the "ontologies are empty
    after the bot adds a shot" bug. The user-facing symptom was
    that ``jf_ontology_skill`` was empty in Postgres even after
    dozens of ``/positive`` calls.
    """
    import psycopg

    dsn = os.environ["JOB_FTCH_TEST_PG_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        # Clean slate
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jf_ontology_skill WHERE canonical = 'pytest_skill'")
            cur.execute("DELETE FROM jf_ontology_role WHERE canonical = 'pytest_role'")

        from job_ftch.infrastructure.ontology.postgres_store import (
            PostgresOntologyStore,
        )

        store = PostgresOntologyStore(dsn=dsn, pool_min=1, pool_max=2)
        try:
            asyncio.run(store.upsert_skill("pytest_skill", alias="pytest_skill", lang="en"))
            asyncio.run(store.upsert_role("pytest_role", alias="pytest_role", lang="en"))
            skills = asyncio.run(store.list_skills())
            roles = asyncio.run(store.list_roles())
            assert "pytest_skill" in skills
            assert "pytest_role" in roles
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jf_ontology_skill WHERE canonical = 'pytest_skill'")
                cur.execute("DELETE FROM jf_ontology_role WHERE canonical = 'pytest_role'")
            asyncio.run(store.close())
