"""PostgreSQL persistent backend for jobs, groups, and search."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from job_ftch.application.contracts import JobGroupStore, JobPersistenceBackend, SearchBackend
from job_ftch.application.registry import (
    register_job_backend,
    register_job_group_store,
    register_search_backend,
)
from job_ftch.domain import (
    Job,
    JobGroup,
    JobRecord,
    compute_identity_fingerprint,
    create_job_group,
    merge_job_into_group,
    remove_job_from_group,
)

from .serialization import dump_group, dump_job, load_group, load_job

try:
    import asyncpg

    _IMPORT_ERROR = None
except ImportError as exc:
    asyncpg = None
    _IMPORT_ERROR = exc


if TYPE_CHECKING:
    from datetime import datetime

    from job_ftch.config import Settings


ALLOWED_SEARCH_LANGUAGES = {"simple", "english", "russian"}


def _coerce_job_record(job: Job | JobRecord) -> JobRecord:
    if isinstance(job, JobRecord):
        return job
    return JobRecord.model_validate(job.model_dump(mode="python"))


@register_job_backend("postgres")
@register_job_group_store("postgres")
@register_search_backend("postgres")
class PostgreSQLJobBackend(JobPersistenceBackend, JobGroupStore, SearchBackend):
    def __init__(self, settings: Settings) -> None:
        if asyncpg is None:
            raise ImportError(
                "PostgreSQL backend requires the 'postgres' extra: pip install job-ftch[postgres]"
            ) from _IMPORT_ERROR
        if not settings.store_dsn:
            raise ValueError("store_dsn is required for postgres backend")

        self.dsn = (
            settings.store_dsn.get_secret_value()
            if hasattr(settings.store_dsn, "get_secret_value")
            else str(settings.store_dsn)
        )
        self.pool_min = settings.store_pool_min
        self.pool_max = settings.store_pool_max
        self.search_language = settings.search_language

        if self.search_language not in ALLOWED_SEARCH_LANGUAGES:
            raise ValueError(f"Unsupported search language: {self.search_language}")

        self._pool: asyncpg.Pool | None = None
        self._schema_initialized = False

        # Stats tracking for RunSummary
        self.new_groups_created = 0
        self.merged_into_group = 0
        self.by_source_kind_new: dict[str, int] = {}
        self.by_source_kind_merged: dict[str, int] = {}

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=self.pool_min,
                max_size=self.pool_max,
            )
            if not self._schema_initialized:
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS jf_migrations (
                            filename TEXT PRIMARY KEY,
                            applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                        )
                    """)
                    migrations_dir = Path(__file__).parent / "migrations"
                    for path in sorted(migrations_dir.glob("*.sql")):
                        if "sqlite" in path.name:
                            continue
                        filename = path.name
                        row = await conn.fetchrow(
                            "SELECT filename FROM jf_migrations WHERE filename = $1", filename
                        )
                        if not row:
                            with open(path, encoding="utf-8") as f:
                                sql = f.read()
                            await conn.execute(sql)
                            await conn.execute(
                                "INSERT INTO jf_migrations (filename) VALUES ($1)", filename
                            )

                self._schema_initialized = True
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def save(self, job: JobRecord) -> None:
        job = _coerce_job_record(job)
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            fingerprint = compute_identity_fingerprint(job)
            # Serialize the read/merge/write sequence for one identity across
            # all workers without taking a global table lock.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", fingerprint)
            group_id = await self._resolve_group_id(
                conn,
                str(job.canonical_url) if job.canonical_url is not None else None,
                fingerprint,
            )

            if group_id:
                row = await conn.fetchrow(
                    "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
                )
                if row:
                    group = load_group(row["raw_json"])
                    updated_group = merge_job_into_group(group, job)
                    # Update stats
                    self.merged_into_group += 1
                    sk = str(job.source_kind)
                    self.by_source_kind_merged[sk] = self.by_source_kind_merged.get(sk, 0) + 1
                else:
                    updated_group = create_job_group(job)
                    self.new_groups_created += 1
                    sk = str(job.source_kind)
                    self.by_source_kind_new[sk] = self.by_source_kind_new.get(sk, 0) + 1
            else:
                updated_group = create_job_group(job)
                self.new_groups_created += 1
                sk = str(job.source_kind)
                self.by_source_kind_new[sk] = self.by_source_kind_new.get(sk, 0) + 1

            await self._persist_group(conn, updated_group)
            await self._persist_job(conn, job, updated_group.group_id)

    async def create(self, job: JobRecord) -> JobGroup:
        job = _coerce_job_record(job)
        # Keep create consistent with the atomic identity path used by save.
        await self.save(job)
        group = await self.find_by_url(str(job.canonical_url)) if job.canonical_url else None
        group = group or await self.find_by_fingerprint(compute_identity_fingerprint(job))
        if group is None:
            raise RuntimeError("Job group was not persisted after atomic save")
        return group

    async def find_by_url(self, canonical_url: str) -> JobGroup | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT group_id FROM jf_job_group_urls WHERE canonical_url = $1",
                str(canonical_url),
            )
            if not row:
                return None
            grow = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", row["group_id"]
            )
            return load_group(grow["raw_json"]) if grow else None

    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT group_id FROM jf_job_group_fingerprints WHERE fingerprint = $1",
                fingerprint,
            )
            if not row:
                return None
            grow = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", row["group_id"]
            )
            return load_group(grow["raw_json"]) if grow else None

    async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT raw_json FROM jf_job_groups WHERE blocking_key = $1 LIMIT $2",
                key,
                limit,
            )
            return [load_group(row["raw_json"]) for row in rows]

    async def merge(self, group_id: str, job: JobRecord, merge_confidence: float = 1.0) -> JobGroup:
        job = _coerce_job_record(job)
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
            )
            if not row:
                raise ValueError(f"Group {group_id} not found.")
            group = load_group(row["raw_json"])
            updated_group = merge_job_into_group(group, job, merge_confidence=merge_confidence)
            await self._persist_group(conn, updated_group)
            await self._persist_job(conn, job, updated_group.group_id)

            # Update stats
            self.merged_into_group += 1
            sk = str(job.source_kind)
            self.by_source_kind_merged[sk] = self.by_source_kind_merged.get(sk, 0) + 1

            return updated_group

    async def replace_member(self, group_id: str, job: JobRecord) -> JobGroup:
        """Persist post-accept enrichment without counting an identity merge."""
        job = _coerce_job_record(job)
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
            )
            if not row:
                raise ValueError(f"Group {group_id} not found.")
            group = load_group(row["raw_json"])
            updated_group = merge_job_into_group(
                group, job, merge_confidence=group.merge_confidence
            )
            await self._persist_group(conn, updated_group)
            await self._persist_job(conn, job, updated_group.group_id)
            return updated_group

    async def _resolve_group_id(
        self,
        conn: asyncpg.Connection,
        canonical_url: str | None,
        fingerprint: str,
    ) -> str | None:
        value = await conn.fetchval(
            """
            SELECT COALESCE(
                (SELECT group_id FROM jf_job_group_urls WHERE canonical_url = $1),
                (SELECT group_id FROM jf_job_group_fingerprints WHERE fingerprint = $2)
            )
            """,
            str(canonical_url) if canonical_url else None,
            fingerprint,
        )
        return str(value) if value is not None else None

    async def _persist_group(self, conn: asyncpg.Connection, group: JobGroup) -> None:
        raw_json = dump_group(group)
        await conn.execute(
            """INSERT INTO jf_job_groups (group_id, raw_json, blocking_key, updated_at)
               VALUES ($1, $2, $3, NOW())
               ON CONFLICT(group_id) DO UPDATE SET raw_json=EXCLUDED.raw_json, blocking_key=EXCLUDED.blocking_key, updated_at=NOW()""",
            group.group_id,
            raw_json,
            group.blocking_key,
        )

        # Batch index maintenance to avoid one round-trip per job.
        url_rows: list[tuple[str, str]] = []
        fingerprint_rows: list[tuple[str, str]] = []
        for job in group.jobs:
            if job.canonical_url:
                url_rows.append((str(job.canonical_url), group.group_id))
            fp = compute_identity_fingerprint(job)
            fingerprint_rows.append((fp, group.group_id))

        if url_rows:
            await conn.executemany(
                "INSERT INTO jf_job_group_urls (canonical_url, group_id) VALUES ($1, $2) ON CONFLICT (canonical_url) DO UPDATE SET group_id=EXCLUDED.group_id",
                url_rows,
            )
        if fingerprint_rows:
            await conn.executemany(
                "INSERT INTO jf_job_group_fingerprints (fingerprint, group_id) VALUES ($1, $2) ON CONFLICT (fingerprint) DO UPDATE SET group_id=EXCLUDED.group_id",
                fingerprint_rows,
            )

    async def _persist_job(self, conn: asyncpg.Connection, job: JobRecord, group_id: str) -> None:
        # Deep copy metadata to avoid mutation of original job
        job_copy = job.model_copy(
            update={
                "metadata": {
                    **job.metadata,
                    "group_id": group_id,
                }
            }
        )
        raw_json = dump_job(job_copy)

        await conn.execute(
            """INSERT INTO jf_jobs (
                job_id, group_id, source_kind, source_name, title, company, 
                company_canonical, description, canonical_url, location, work_mode, raw_json, updated_at, fts_vector
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW(),
                to_tsvector($13::regconfig, coalesce($5, '') || ' ' || coalesce($6, '') || ' ' || coalesce($7, '') || ' ' || coalesce($8, ''))
            )
            ON CONFLICT(job_id) DO UPDATE SET 
                group_id=EXCLUDED.group_id,
                source_kind=EXCLUDED.source_kind,
                source_name=EXCLUDED.source_name,
                title=EXCLUDED.title,
                company=EXCLUDED.company,
                company_canonical=EXCLUDED.company_canonical,
                description=EXCLUDED.description,
                canonical_url=EXCLUDED.canonical_url,
                location=EXCLUDED.location,
                work_mode=EXCLUDED.work_mode,
                raw_json=EXCLUDED.raw_json,
                updated_at=NOW(),
                fts_vector=to_tsvector($13::regconfig, coalesce($5, '') || ' ' || coalesce($6, '') || ' ' || coalesce($7, '') || ' ' || coalesce($8, ''))
            """,
            job.job_id,
            group_id,
            str(job.source_kind),
            job.source_name,
            job.title,
            job.company,
            job.company_canonical,
            job.description,
            str(job.canonical_url) if job.canonical_url else None,
            job.location,
            str(job.work_mode),
            raw_json,
            self.search_language,
        )

    async def get_job(self, job_id: str) -> JobRecord | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT raw_json FROM jf_jobs WHERE job_id = $1", job_id)
            if row:
                return load_job(row["raw_json"])
        return None

    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT raw_json FROM jf_jobs ORDER BY updated_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
            return [load_job(row["raw_json"]) for row in rows]

    async def count_jobs(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT COUNT(*) FROM jf_jobs")
            return int(value or 0)

    async def get_group(self, group_id: str) -> JobGroup | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
            )
            if row:
                return load_group(row["raw_json"])
        return None

    async def delete(self, job_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            group_id = await conn.fetchval("SELECT group_id FROM jf_jobs WHERE job_id = $1", job_id)
            if not group_id:
                return

            await conn.execute("DELETE FROM jf_jobs WHERE job_id = $1", job_id)

            grow = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
            )
            if grow:
                group = load_group(grow["raw_json"])
                updated_group = remove_job_from_group(group, job_id)
                if updated_group is None:
                    await conn.execute("DELETE FROM jf_job_groups WHERE group_id = $1", group_id)
                    await conn.execute(
                        "DELETE FROM jf_job_group_urls WHERE group_id = $1", group_id
                    )
                    await conn.execute(
                        "DELETE FROM jf_job_group_fingerprints WHERE group_id = $1",
                        group_id,
                    )
                else:
                    # Refresh indexes by clearing and re-persisting
                    await conn.execute(
                        "DELETE FROM jf_job_group_urls WHERE group_id = $1", group_id
                    )
                    await conn.execute(
                        "DELETE FROM jf_job_group_fingerprints WHERE group_id = $1",
                        group_id,
                    )
                    await self._persist_group(conn, updated_group)

    async def list_groups(self, limit: int = 100, since: datetime | None = None) -> list[JobGroup]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if since is None:
                rows = await conn.fetch(
                    "SELECT raw_json FROM jf_job_groups ORDER BY updated_at DESC LIMIT $1",
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT raw_json
                    FROM jf_job_groups
                    WHERE raw_json->'canonical_job'->>'fetched_at' IS NULL
                       OR (raw_json->'canonical_job'->>'fetched_at')::timestamptz >= $1
                    ORDER BY updated_at DESC
                    LIMIT $2
                    """,
                    since,
                    limit,
                )
            return [load_group(row["raw_json"]) for row in rows]

    async def count(self, since: datetime | None = None) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if since is None:
                val = await conn.fetchval("SELECT COUNT(*) FROM jf_job_groups")
            else:
                val = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM jf_job_groups
                    WHERE raw_json->'canonical_job'->>'fetched_at' IS NULL
                       OR (raw_json->'canonical_job'->>'fetched_at')::timestamptz >= $1
                    """,
                    since,
                )
            return val or 0

    async def clear(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            count = await conn.fetchval("SELECT COUNT(*) FROM jf_job_groups")
            await conn.execute("DELETE FROM jf_jobs")
            await conn.execute("DELETE FROM jf_job_group_urls")
            await conn.execute("DELETE FROM jf_job_group_fingerprints")
            await conn.execute("DELETE FROM jf_job_groups")
            return int(count or 0)

    async def search(self, query: str, limit: int = 20) -> list[JobGroup]:
        q = query.strip()
        if not q:
            return await self.list_groups(limit)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            overfetch = limit * 5
            rows = await conn.fetch(
                """
                SELECT group_id FROM jf_jobs
                WHERE fts_vector @@ plainto_tsquery($3::regconfig, $1)
                ORDER BY ts_rank(fts_vector, plainto_tsquery($3::regconfig, $1)) DESC
                LIMIT $2
                """,
                q,
                overfetch,
                self.search_language,
            )

            seen = set()
            group_ids = []
            for row in rows:
                gid = row["group_id"]
                if gid not in seen:
                    seen.add(gid)
                    group_ids.append(gid)
                    if len(group_ids) >= limit:
                        break

            if not group_ids:
                return []

            grows = await conn.fetch(
                "SELECT group_id, raw_json FROM jf_job_groups WHERE group_id = ANY($1)",
                group_ids,
            )
            group_map = {row["group_id"]: load_group(row["raw_json"]) for row in grows}
            return [group_map[gid] for gid in group_ids if gid in group_map]
