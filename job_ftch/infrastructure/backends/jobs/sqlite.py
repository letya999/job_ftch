"""SQLite persistent backend for jobs, groups, and search."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

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

if TYPE_CHECKING:
    from datetime import datetime

    from job_ftch.config import Settings


def normalize_fts5_query(query: str) -> str:
    """Normalize query for FTS5."""
    # Trim and collapse whitespace
    q = query.strip()
    # Replace punctuation/operators with spaces
    # FTS5 uses specific operators (*, ^, OR, AND, NOT, etc)
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    q = " ".join(q.split())
    return q


def _coerce_job_record(job: Job | JobRecord) -> JobRecord:
    if isinstance(job, JobRecord):
        return job
    return JobRecord.model_validate(job.model_dump(mode="python"))


@register_job_backend("sqlite")
@register_job_group_store("sqlite")
@register_search_backend("sqlite")
class SQLiteJobBackend(JobPersistenceBackend, JobGroupStore, SearchBackend):
    def __init__(self, settings: Settings) -> None:
        self.db_path = settings.job_store_path or settings.store_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

        # Stats tracking for RunSummary (Compatibility with InMemoryJobGroupStore)
        self.new_groups_created = 0
        self.merged_into_group = 0
        self.by_source_kind_new: dict[str, int] = {}
        self.by_source_kind_merged: dict[str, int] = {}

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            # ensure dir exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self.db_path)
            try:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS jf_migrations (
                        filename TEXT PRIMARY KEY,
                        applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    )
                """)
                migrations_dir = Path(__file__).parent / "migrations"
                for path in sorted(migrations_dir.glob("*.sql")):
                    if "postgres" in path.name:
                        continue
                    filename = path.name
                    async with conn.execute(
                        "SELECT filename FROM jf_migrations WHERE filename = ?", (filename,)
                    ) as cur:
                        row = await cur.fetchone()
                    if not row:
                        with open(path, encoding="utf-8") as f:
                            sql = f.read()
                        await conn.executescript(sql)
                        await conn.execute(
                            "INSERT INTO jf_migrations (filename) VALUES (?)", (filename,)
                        )

                await conn.commit()
                self._conn = conn
            except Exception:
                await conn.close()
                raise
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        try:
            conn = await self._get_conn()
            async with conn.execute("SELECT 1") as cursor:
                await cursor.fetchone()
            return True
        except Exception:
            return False

    async def save(self, job: JobRecord) -> None:
        """Required behavior:
        1. compute canonical URL key if present
        2. compute identity fingerprint
        3. find existing group by URL
        4. otherwise find existing group by fingerprint
        5. if group exists, merge job into group
        6. otherwise create a new group
        7. persist group JSON
        8. persist job JSON with group_id in metadata
        9. refresh URL index for all group jobs with canonical_url
        10. refresh fingerprint index
        11. refresh FTS row for saved job
        """
        job = _coerce_job_record(job)
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                group_id: str | None = None

                # Check url index
                if job.canonical_url:
                    async with conn.execute(
                        "SELECT group_id FROM jf_job_group_urls WHERE canonical_url = ?",
                        (str(job.canonical_url),),
                    ) as cur:
                        row = await cur.fetchone()
                        if row:
                            group_id = row[0]

                # Check fingerprint index
                fingerprint = compute_identity_fingerprint(job)
                if not group_id:
                    async with conn.execute(
                        "SELECT group_id FROM jf_job_group_fingerprints WHERE fingerprint = ?",
                        (fingerprint,),
                    ) as cur:
                        row = await cur.fetchone()
                        if row:
                            group_id = row[0]

                if group_id:
                    # load existing group
                    async with conn.execute(
                        "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (group_id,)
                    ) as cur:
                        row = await cur.fetchone()
                    if row:
                        group = load_group(row[0])
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
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def create(self, job: JobRecord) -> JobGroup:
        job = _coerce_job_record(job)
        # ``save`` owns the transactional URL/fingerprint lookup. Reusing it
        # here makes create idempotent across SQLite connections instead of
        # letting a second writer overwrite the identity index.
        await self.save(job)
        group = await self.find_by_url(str(job.canonical_url)) if job.canonical_url else None
        group = group or await self.find_by_fingerprint(compute_identity_fingerprint(job))
        if group is None:
            raise RuntimeError("Job group was not persisted after atomic save")
        return group

    async def merge(self, group_id: str, job: JobRecord, merge_confidence: float = 1.0) -> JobGroup:
        job = _coerce_job_record(job)
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (group_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Group {group_id} not found.")
                group = load_group(row[0])
                updated_group = merge_job_into_group(group, job, merge_confidence=merge_confidence)
                await self._persist_group(conn, updated_group)
                await self._persist_job(conn, job, updated_group.group_id)

                # Update stats
                self.merged_into_group += 1
                sk = str(job.source_kind)
                self.by_source_kind_merged[sk] = self.by_source_kind_merged.get(sk, 0) + 1

                await conn.commit()
                return updated_group
            except Exception:
                await conn.rollback()
                raise

    async def replace_member(self, group_id: str, job: JobRecord) -> JobGroup:
        """Persist post-accept enrichment without counting an identity merge."""
        job = _coerce_job_record(job)
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (group_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Group {group_id} not found.")
                group = load_group(row[0])
                updated_group = merge_job_into_group(
                    group, job, merge_confidence=group.merge_confidence
                )
                await self._persist_group(conn, updated_group)
                await self._persist_job(conn, job, updated_group.group_id)
                await conn.commit()
                return updated_group
            except Exception:
                await conn.rollback()
                raise

    async def _persist_group(self, conn: aiosqlite.Connection, group: JobGroup) -> None:
        raw_json = dump_group(group)
        await conn.execute(
            """INSERT INTO jf_job_groups (group_id, raw_json, blocking_key, updated_at)
               VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(group_id) DO UPDATE SET raw_json=excluded.raw_json, blocking_key=excluded.blocking_key, updated_at=excluded.updated_at""",
            (group.group_id, raw_json, group.blocking_key),
        )

        # update URL/fingerprint indexes
        for job in group.jobs:
            if job.canonical_url:
                await conn.execute(
                    "INSERT OR REPLACE INTO jf_job_group_urls (canonical_url, group_id) VALUES (?, ?)",
                    (str(job.canonical_url), group.group_id),
                )
            fp = compute_identity_fingerprint(job)
            await conn.execute(
                "INSERT OR REPLACE INTO jf_job_group_fingerprints (fingerprint, group_id) VALUES (?, ?)",
                (fp, group.group_id),
            )

    async def _persist_job(self, conn: aiosqlite.Connection, job: JobRecord, group_id: str) -> None:
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
                company_canonical, description, canonical_url, location, work_mode, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT(job_id) DO UPDATE SET 
                group_id=excluded.group_id,
                source_kind=excluded.source_kind,
                source_name=excluded.source_name,
                title=excluded.title,
                company=excluded.company,
                company_canonical=excluded.company_canonical,
                description=excluded.description,
                canonical_url=excluded.canonical_url,
                location=excluded.location,
                work_mode=excluded.work_mode,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
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
            ),
        )

        # update FTS
        await conn.execute("DELETE FROM jf_jobs_fts WHERE job_id = ?", (job.job_id,))
        await conn.execute(
            """INSERT INTO jf_jobs_fts (job_id, group_id, title, company, company_canonical, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job.job_id,
                group_id,
                job.title,
                job.company,
                job.company_canonical,
                job.description,
            ),
        )

    async def get_job(self, job_id: str) -> JobRecord | None:
        conn = await self._get_conn()
        async with conn.execute("SELECT raw_json FROM jf_jobs WHERE job_id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
        if row:
            return load_job(row[0])
        return None

    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT raw_json FROM jf_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [load_job(row[0]) for row in rows]

    async def count_jobs(self) -> int:
        conn = await self._get_conn()
        async with conn.execute("SELECT COUNT(*) FROM jf_jobs") as cur:
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def get_group(self, group_id: str) -> JobGroup | None:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return load_group(row[0])
        return None

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. load job to find group_id
                async with conn.execute(
                    "SELECT group_id FROM jf_jobs WHERE job_id = ?", (job_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    await conn.commit()
                    return  # no-op

                group_id = row[0]

                # 2. delete job row
                await conn.execute("DELETE FROM jf_jobs WHERE job_id = ?", (job_id,))
                # 3. delete FTS row
                await conn.execute("DELETE FROM jf_jobs_fts WHERE job_id = ?", (job_id,))

                # 4. load group to update it
                async with conn.execute(
                    "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (group_id,)
                ) as cur:
                    grow = await cur.fetchone()
                if grow:
                    group = load_group(grow[0])
                    # 5. remove job from group
                    updated_group = remove_job_from_group(group, job_id)
                    if updated_group is None:
                        # 6. if group becomes empty, delete group and its URL/fingerprint indexes
                        await conn.execute(
                            "DELETE FROM jf_job_groups WHERE group_id = ?", (group_id,)
                        )
                        await conn.execute(
                            "DELETE FROM jf_job_group_urls WHERE group_id = ?", (group_id,)
                        )
                        await conn.execute(
                            "DELETE FROM jf_job_group_fingerprints WHERE group_id = ?", (group_id,)
                        )
                    else:
                        # 7. otherwise persist updated group and refresh indexes
                        # Clear new index entries for this group
                        await conn.execute(
                            "DELETE FROM jf_job_group_urls WHERE group_id = ?", (group_id,)
                        )
                        await conn.execute(
                            "DELETE FROM jf_job_group_fingerprints WHERE group_id = ?", (group_id,)
                        )
                        await self._persist_group(conn, updated_group)

                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def find_by_url(self, canonical_url: str) -> JobGroup | None:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT group_id FROM jf_job_group_urls WHERE canonical_url = ?", (str(canonical_url),)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        async with conn.execute(
            "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (row[0],)
        ) as cur:
            grow = await cur.fetchone()
        return load_group(grow[0]) if grow else None

    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT group_id FROM jf_job_group_fingerprints WHERE fingerprint = ?", (fingerprint,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        async with conn.execute(
            "SELECT raw_json FROM jf_job_groups WHERE group_id = ?", (row[0],)
        ) as cur:
            grow = await cur.fetchone()
        return load_group(grow[0]) if grow else None

    async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT raw_json FROM jf_job_groups WHERE blocking_key = ? LIMIT ?",
            (key, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [load_group(row[0]) for row in rows]

    async def list_groups(self, limit: int = 100, since: datetime | None = None) -> list[JobGroup]:
        conn = await self._get_conn()
        if since is None:
            query = "SELECT raw_json FROM jf_job_groups ORDER BY updated_at DESC LIMIT ?"
            params: tuple[object, ...] = (limit,)
        else:
            query = """
                SELECT raw_json
                FROM jf_job_groups
                WHERE json_extract(raw_json, '$.canonical_job.fetched_at') IS NULL
                   OR json_extract(raw_json, '$.canonical_job.fetched_at') >= ?
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params = (since.isoformat(), limit)
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [load_group(row[0]) for row in rows]

    async def count(self, since: datetime | None = None) -> int:
        conn = await self._get_conn()
        if since is None:
            query = "SELECT COUNT(*) FROM jf_job_groups"
            params: tuple[object, ...] = ()
        else:
            query = """
                SELECT COUNT(*)
                FROM jf_job_groups
                WHERE json_extract(raw_json, '$.canonical_job.fetched_at') IS NULL
                   OR json_extract(raw_json, '$.canonical_job.fetched_at') >= ?
            """
            params = (since.isoformat(),)
        async with conn.execute(query, params) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def clear(self) -> int:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute("SELECT COUNT(*) FROM jf_job_groups") as cur:
                    row = await cur.fetchone()
                    count = row[0] if row else 0
                await conn.execute("DELETE FROM jf_jobs_fts")
                await conn.execute("DELETE FROM jf_jobs")
                await conn.execute("DELETE FROM jf_job_group_urls")
                await conn.execute("DELETE FROM jf_job_group_fingerprints")
                await conn.execute("DELETE FROM jf_job_groups")
                await conn.commit()
                return int(count)
            except Exception:
                await conn.rollback()
                raise

    async def search(self, query: str, limit: int = 20) -> list[JobGroup]:
        norm_query = normalize_fts5_query(query)
        conn = await self._get_conn()

        if not norm_query:
            return await self.list_groups(limit)

        overfetch = limit * 5
        async with conn.execute(
            """SELECT group_id FROM jf_jobs_fts 
               WHERE jf_jobs_fts MATCH ? 
               ORDER BY bm25(jf_jobs_fts) 
               LIMIT ?""",
            (norm_query, overfetch),
        ) as cur:
            rows = await cur.fetchall()

        # dedupe group_id preserving rank order
        seen = set()
        group_ids = []
        for (gid,) in rows:
            if gid not in seen:
                seen.add(gid)
                group_ids.append(gid)
                if len(group_ids) >= limit:
                    break

        if not group_ids:
            return []

        # load groups
        placeholders = ",".join("?" * len(group_ids))
        group_query = (
            # Marker count is generated locally; all group IDs remain bound parameters.
            "SELECT group_id, raw_json FROM jf_job_groups WHERE group_id IN ("  # nosec B608
            + placeholders
            + ")"
        )
        # Only the number of parameter markers is interpolated; values stay bound.
        async with conn.execute(group_query, group_ids) as cur:
            grows = await cur.fetchall()

        group_map = {row[0]: load_group(row[1]) for row in grows}
        return [group_map[gid] for gid in group_ids if gid in group_map]
