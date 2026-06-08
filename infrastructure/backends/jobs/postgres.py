"""PostgreSQL persistent backend for jobs, groups, and search."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from application.contracts import JobGroupStore, JobPersistenceBackend, SearchBackend
from application.registry import (
    register_job_backend,
    register_job_group_store,
    register_search_backend,
)
from domain import (
    Job,
    JobGroup,
    compute_identity_fingerprint,
    create_job_group,
    merge_job_into_group,
    remove_job_from_group,
)

from .serialization import dump_group, dump_job, load_group, load_job

try:
    import asyncpg
except ImportError:
    asyncpg = None

if TYPE_CHECKING:
    from config import Settings


ALLOWED_SEARCH_LANGUAGES = {"simple", "english", "russian"}


@register_job_backend("postgres")
@register_job_group_store("postgres")
@register_search_backend("postgres")
class PostgreSQLJobBackend(JobPersistenceBackend, JobGroupStore, SearchBackend):
    def __init__(self, settings: Settings) -> None:
        if asyncpg is None:
            raise ImportError("asyncpg is required for postgres backend")
        if not settings.store_dsn:
            raise ValueError("store_dsn is required for postgres backend")

        self.dsn = settings.store_dsn
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
                    migration_path = Path(__file__).parent / "migrations" / "001_postgres_jobs.sql"
                    with open(migration_path, encoding="utf-8") as f:
                        schema = f.read()
                    await conn.execute(schema)
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

    async def save(self, job: Job) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            group_id: str | None = None

            # Check url index
            if job.canonical_url:
                group_id = await conn.fetchval(
                    "SELECT group_id FROM jf_job_group_urls WHERE canonical_url = $1",
                    str(job.canonical_url),
                )

            # Check fingerprint index
            fingerprint = compute_identity_fingerprint(job)
            if not group_id:
                group_id = await conn.fetchval(
                    "SELECT group_id FROM jf_job_group_fingerprints WHERE fingerprint = $1",
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

    async def create(self, job: Job) -> JobGroup:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            group = create_job_group(job)
            await self._persist_group(conn, group)
            await self._persist_job(conn, job, group.group_id)

            # Update stats
            self.new_groups_created += 1
            sk = str(job.source_kind)
            self.by_source_kind_new[sk] = self.by_source_kind_new.get(sk, 0) + 1

            return group

    async def merge(self, group_id: str, job: Job) -> JobGroup:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT raw_json FROM jf_job_groups WHERE group_id = $1", group_id
            )
            if not row:
                raise ValueError(f"Group {group_id} not found.")
            group = load_group(row["raw_json"])
            updated_group = merge_job_into_group(group, job)
            await self._persist_group(conn, updated_group)
            await self._persist_job(conn, job, updated_group.group_id)

            # Update stats
            self.merged_into_group += 1
            sk = str(job.source_kind)
            self.by_source_kind_merged[sk] = self.by_source_kind_merged.get(sk, 0) + 1

            return updated_group

    async def _persist_group(self, conn: asyncpg.Connection, group: JobGroup) -> None:
        raw_json = dump_group(group)
        await conn.execute(
            """INSERT INTO jf_job_groups (group_id, raw_json, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT(group_id) DO UPDATE SET raw_json=EXCLUDED.raw_json, updated_at=NOW()""",
            group.group_id,
            raw_json,
        )

        # update URL/fingerprint indexes
        for job in group.jobs:
            if job.canonical_url:
                await conn.execute(
                    "INSERT INTO jf_job_group_urls (canonical_url, group_id) VALUES ($1, $2) ON CONFLICT (canonical_url) DO UPDATE SET group_id=EXCLUDED.group_id",
                    str(job.canonical_url),
                    group.group_id,
                )
            fp = compute_identity_fingerprint(job)
            await conn.execute(
                "INSERT INTO jf_job_group_fingerprints (fingerprint, group_id) VALUES ($1, $2) ON CONFLICT (fingerprint) DO UPDATE SET group_id=EXCLUDED.group_id",
                fp,
                group.group_id,
            )

    async def _persist_job(self, conn: asyncpg.Connection, job: Job, group_id: str) -> None:
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

        # SQL safe building of FTS vector
        # self.search_language is already whitelisted in __init__
        fts_query = f"to_tsvector('{self.search_language}', coalesce($5, '') || ' ' || coalesce($6, '') || ' ' || coalesce($7, '') || ' ' || coalesce($8, ''))"

        await conn.execute(
            f"""INSERT INTO jf_jobs (
                job_id, group_id, source_kind, source_name, title, company, 
                company_canonical, description, canonical_url, location, work_mode, raw_json, updated_at, fts_vector
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW(), {fts_query})
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
                fts_vector={fts_query}
            """,
            job.stable_id,
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
        )

    async def get_job(self, job_id: str) -> Job | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT raw_json FROM jf_jobs WHERE job_id = $1", job_id)
            if row:
                return load_job(row["raw_json"])
        return None

    async def list_jobs(self, limit: int, offset: int) -> list[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT raw_json FROM jf_jobs ORDER BY updated_at DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
            return [load_job(row["raw_json"]) for row in rows]

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

    async def list_groups(self, limit: int = 100) -> list[JobGroup]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT raw_json FROM jf_job_groups ORDER BY updated_at DESC LIMIT $1",
                limit,
            )
            return [load_group(row["raw_json"]) for row in rows]

    async def count(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT COUNT(*) FROM jf_job_groups")
            return val or 0

    async def search(self, query: str, limit: int = 20) -> list[JobGroup]:
        q = query.strip()
        if not q:
            return await self.list_groups(limit)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            overfetch = limit * 5
            # Safe FTS query
            fts_query_sql = f"plainto_tsquery('{self.search_language}', $1)"

            rows = await conn.fetch(
                f"""
                SELECT group_id FROM jf_jobs
                WHERE fts_vector @@ {fts_query_sql}
                ORDER BY ts_rank(fts_vector, {fts_query_sql}) DESC
                LIMIT $2
            """,  # nosec B608
                q,
                overfetch,
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
