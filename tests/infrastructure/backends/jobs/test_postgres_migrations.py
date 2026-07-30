from pathlib import Path

import asyncpg
import pytest

from job_ftch.infrastructure.backends.jobs.postgres import PostgreSQLJobBackend


class FakeSettings:
    store_dsn = "postgresql://postgres:postgres@localhost:5432/postgres"
    store_pool_min = 1
    store_pool_max = 2
    search_language = "simple"


@pytest.mark.asyncio
async def test_postgres_migrations_order_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = PostgreSQLJobBackend(FakeSettings())

    # Create fake migrations
    m_dir = tmp_path / "migrations"
    m_dir.mkdir()
    (m_dir / "002_postgres_b.sql").write_text("CREATE TABLE t2 (id INT);")
    (m_dir / "001_postgres_a.sql").write_text("CREATE TABLE t1 (id INT);")
    (m_dir / "003_sqlite_c.sql").write_text("CREATE TABLE t3 (id INT);")
    (m_dir / "004_postgres_error.sql").write_text("SYNTAX ERROR;")

    original_path = Path

    class FakePath(type(Path())):
        def __new__(cls, *args, **kwargs):
            if args and str(args[0]).endswith("postgres.py"):

                class FakePyPath:
                    @property
                    def parent(self):
                        return tmp_path

                return FakePyPath()
            return original_path(*args, **kwargs)

    monkeypatch.setattr("job_ftch.infrastructure.backends.jobs.postgres.Path", FakePath)

    # Use a real connection but we can mock pool.acquire or just use a mock connection
    # Wait, the prompt says "deterministic migration-loader tests for both backends. They must assert selected filenames/order and that an injected execute failure is raised rather than hidden."
    # If postgres is not running, we could use a mock.

    class FakeConn:
        def __init__(self):
            self.executed = []
            self.tables = {"jf_migrations": []}
            self.creates = []

        async def execute(self, sql, *args):
            if "CREATE TABLE IF NOT EXISTS jf_migrations" in sql:
                self.creates.append("jf_migrations")
            elif "INSERT INTO jf_migrations" in sql:
                self.tables["jf_migrations"].append(args[0])
            elif "SYNTAX ERROR" in sql:
                import asyncpg

                raise asyncpg.exceptions.PostgresSyntaxError("Syntax error")
            else:
                self.executed.append(sql)

        async def fetchrow(self, sql, *args):
            if "SELECT filename FROM jf_migrations" in sql:
                if args[0] in self.tables["jf_migrations"]:
                    return {"filename": args[0]}
                return None
            return None

    class FakePool:
        class ConnContext:
            def __init__(self, conn):
                self.conn = conn

            async def __aenter__(self):
                return self.conn

            async def __aexit__(self, exc_type, exc, tb):
                pass

        def __init__(self):
            self.conn = FakeConn()

        def acquire(self):
            return self.ConnContext(self.conn)

        async def close(self):
            pass

    async def fake_create_pool(*args, **kwargs):
        return FakePool()

    monkeypatch.setattr(
        "job_ftch.infrastructure.backends.jobs.postgres.asyncpg.create_pool", fake_create_pool
    )

    with pytest.raises(asyncpg.exceptions.PostgresSyntaxError):
        await backend._get_pool()

    # The pool was mocked, so we can check the connection
    assert backend._pool.conn.executed == ["CREATE TABLE t1 (id INT);", "CREATE TABLE t2 (id INT);"]
    assert backend._pool.conn.tables["jf_migrations"] == [
        "001_postgres_a.sql",
        "002_postgres_b.sql",
    ]
