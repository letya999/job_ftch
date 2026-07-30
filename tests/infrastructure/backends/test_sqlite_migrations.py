from pathlib import Path

import aiosqlite
import pytest

from job_ftch.config import Settings
from job_ftch.infrastructure.backends.jobs.sqlite import SQLiteJobBackend


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        job_backend="sqlite", search_backend="sqlite", job_store_path=tmp_path / "test.db"
    )


@pytest.mark.asyncio
async def test_sqlite_migrations_order_and_failure(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = SQLiteJobBackend(settings)

    # Create fake migrations
    m_dir = tmp_path / "migrations"
    m_dir.mkdir()
    (m_dir / "002_sqlite_b.sql").write_text("CREATE TABLE t2 (id INT);")
    (m_dir / "001_sqlite_a.sql").write_text("CREATE TABLE t1 (id INT);")
    (m_dir / "003_postgres_c.sql").write_text("CREATE TABLE t3 (id INT);")
    (m_dir / "004_sqlite_error.sql").write_text("SYNTAX ERROR;")

    # Patch Path
    original_path = Path

    class FakePath(type(Path())):
        def __new__(cls, *args, **kwargs):
            if args and str(args[0]).endswith("sqlite.py"):

                class FakePyPath:
                    @property
                    def parent(self):
                        return tmp_path

                return FakePyPath()
            return original_path(*args, **kwargs)

    monkeypatch.setattr("job_ftch.infrastructure.backends.jobs.sqlite.Path", FakePath)

    with pytest.raises(aiosqlite.OperationalError):
        await backend._get_conn()

    async with aiosqlite.connect(settings.job_store_path) as conn:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = [row[0] for row in await cur.fetchall()]
        assert "t1" in tables
        assert "t2" in tables
        assert "jf_migrations" in tables

        async with conn.execute("SELECT filename FROM jf_migrations ORDER BY filename") as cur:
            applied = [row[0] for row in await cur.fetchall()]
        assert applied == ["001_sqlite_a.sql", "002_sqlite_b.sql"]
