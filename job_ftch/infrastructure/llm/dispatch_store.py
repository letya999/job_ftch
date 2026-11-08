"""Optional PostgreSQL request/result store and atomic Procrastinate dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import procrastinate
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from job_ftch.application.llm_dispatch import LLMRequestDeferredError

if TYPE_CHECKING:
    from psycopg import AsyncConnection
    from psycopg_pool import AsyncConnectionPool

    from job_ftch.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jf_llm_requests (
    request_id text PRIMARY KEY, tenant_id text NOT NULL, profile text NOT NULL,
    scope text NOT NULL, revision text NOT NULL, session_id text NOT NULL,
    payload jsonb NOT NULL, state text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0, reserved_tokens bigint NOT NULL DEFAULT 0,
    expires_at timestamptz NOT NULL, next_run_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    response jsonb, response_status integer, response_headers jsonb,
    error text, job_id bigint
);
CREATE INDEX IF NOT EXISTS jf_llm_requests_state ON jf_llm_requests(state, updated_at);
CREATE TABLE IF NOT EXISTS jf_llm_scopes (
    scope text PRIMARY KEY, not_before timestamptz NOT NULL DEFAULT now(),
    window_start timestamptz NOT NULL DEFAULT now(), calls bigint NOT NULL DEFAULT 0,
    tokens bigint NOT NULL DEFAULT 0, paused_reason text
);
ALTER TABLE jf_llm_requests ADD COLUMN IF NOT EXISTS logical_run_id text;
ALTER TABLE jf_llm_requests ADD COLUMN IF NOT EXISTS operation text NOT NULL DEFAULT 'work';
ALTER TABLE jf_llm_requests ADD COLUMN IF NOT EXISTS actual_tokens bigint;
CREATE TABLE IF NOT EXISTS jf_llm_model_state (
    scope text NOT NULL, model text NOT NULL,
    not_before timestamptz NOT NULL DEFAULT now(), failures integer NOT NULL DEFAULT 0,
    circuit_open boolean NOT NULL DEFAULT false, reason text, last_failure_at timestamptz,
    PRIMARY KEY(scope,model)
);
CREATE TABLE IF NOT EXISTS jf_llm_runs (
    run_id text PRIMARY KEY, tenant_id text NOT NULL, selector text NOT NULL,
    routes text NOT NULL, state text NOT NULL DEFAULT 'active',
    not_before timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
    summary jsonb, report_request_id text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jf_llm_runs_active ON jf_llm_runs(tenant_id,selector,state);
ALTER TABLE jf_llm_runs ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE jf_llm_runs ADD COLUMN IF NOT EXISTS source_run_ids text[] NOT NULL DEFAULT '{}';
"""


def route_revision(settings: Settings, name: str) -> str:
    profile = settings.llm_provider_profiles[name]
    value = json.dumps(
        [
            profile.base_url,
            profile.credential_ref,
            profile.quota_scope,
            profile.egress_ready_url,
            profile.account_generation,
        ]
    )
    return hashlib.sha256(value.encode()).hexdigest()


def profile_revision(settings: Settings, name: str) -> str:
    profile = settings.llm_provider_profiles[name]
    policy = settings.llm_dispatch_policies[profile.quota_scope or name]
    if profile.cliproxy_dispatch:
        # Scheduling changes must not invalidate saved inference or parked work.
        value = json.dumps(
            profile.model_dump(
                mode="json", exclude={"timeout_seconds", "max_retries", "session_affinity"}
            ),
            sort_keys=True,
        )
    else:
        # Preserve ADR 106 request identity for existing generic queued routes.
        value = json.dumps(
            [
                profile.model_dump(mode="json", exclude={"gateway", "account_generation"}),
                policy.model_dump(
                    exclude={
                        "jitter_seconds",
                        "run_jitter_seconds",
                        "preflight_cache_seconds",
                        "circuit_failure_threshold",
                        "circuit_cooldown_seconds",
                        "circuit_max_cooldown_seconds",
                        "run_summary_enabled",
                    }
                ),
            ],
            sort_keys=True,
        )
    return hashlib.sha256(value.encode()).hexdigest()


def queue_dsn(settings: Settings) -> str:
    raw = settings.llm_queue_dsn or settings.store_dsn
    value = raw.get_secret_value() if isinstance(raw, SecretStr) else str(raw or "")
    if not value.startswith(("postgresql://", "postgres://")):
        raise ValueError("Queued LLM execution requires a PostgreSQL DSN")
    return value


class DispatchStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connector = procrastinate.PsycopgConnector(
            conninfo=queue_dsn(settings),
            min_size=1,
            max_size=4,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000",
            },
        )
        self.app = procrastinate.App(connector=self.connector)
        self._opened = False
        self._open_lock = asyncio.Lock()

    @property
    def pool(self) -> AsyncConnectionPool[AsyncConnection[dict[str, Any]]]:
        return cast("AsyncConnectionPool[AsyncConnection[dict[str, Any]]]", self.connector.pool)

    async def open(self) -> None:
        async with self._open_lock:
            if not self._opened:
                await self.app.open_async()
                self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self.app.close_async()
            self._opened = False

    async def initialize(self) -> None:
        await self.open()
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(1068317)")
            row = await (
                await conn.execute("SELECT to_regclass('procrastinate_jobs') AS existing")
            ).fetchone()
            if row and row["existing"] is None:
                # Schema initialization runs before workers are started.
                await self.app.schema_manager.apply_schema_async()
            await conn.execute(SCHEMA)

    async def submit(
        self,
        name: str,
        tenant: str,
        session: str,
        payload: dict[str, Any],
        *,
        refresh_seconds: int | None = None,
        logical_run_id: str | None = None,
        operation: str = "work",
        item_identity: str = "",
    ) -> str:
        await self.open()
        profile = self.settings.llm_provider_profiles[name]
        scope = profile.quota_scope or name
        policy = self.settings.llm_dispatch_policies[scope]
        revision = profile_revision(self.settings, name)
        canonical = json.dumps([tenant, name, revision, session, payload], sort_keys=True)
        if profile.cliproxy_dispatch:
            canonical = json.dumps(
                [
                    tenant,
                    name,
                    revision,
                    None if operation == "preflight" else logical_run_id,
                    item_identity
                    or (session if not logical_run_id and operation != "preflight" else ""),
                    operation,
                    payload,
                ],
                sort_keys=True,
            )
        request_id = hashlib.sha256(canonical.encode()).hexdigest()
        expires = datetime.now(UTC) + timedelta(seconds=policy.request_ttl_seconds)
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (tenant,))
            existing = await (
                await conn.execute(
                    "SELECT state,updated_at FROM jf_llm_requests WHERE request_id=%s",
                    (request_id,),
                )
            ).fetchone()
            if existing:
                if (
                    refresh_seconds is None
                    or existing["state"] in {"pending", "running", "uncertain"}
                    or existing["updated_at"] + timedelta(seconds=refresh_seconds)
                    > datetime.now(UTC)
                ):
                    return request_id
                await conn.execute("DELETE FROM jf_llm_requests WHERE request_id=%s", (request_id,))
            count = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM jf_llm_requests WHERE tenant_id=%s "
                    "AND state IN ('pending','running')",
                    (tenant,),
                )
            ).fetchone()
            if count and count["n"] >= policy.max_pending_per_tenant:
                if profile.cliproxy_dispatch:
                    raise LLMRequestDeferredError(
                        request_id,
                        datetime.now(UTC) + timedelta(seconds=30),
                        reason="queue_capacity",
                    )
                raise RuntimeError("LLM queue tenant capacity exceeded")
            await conn.execute(
                "INSERT INTO jf_llm_requests "
                "(request_id,tenant_id,profile,scope,revision,session_id,payload,expires_at,logical_run_id,operation) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    request_id,
                    tenant,
                    name,
                    scope,
                    revision,
                    session,
                    Jsonb(payload),
                    expires,
                    logical_run_id,
                    operation,
                ),
            )
            job_id = await self.app.configure_task(
                "job_ftch.llm.execute",
                connection=conn,
                queue="llm",
                lock=f"llm:{scope}",
            ).defer_async(request_id=request_id)
            await conn.execute(
                "UPDATE jf_llm_requests SET job_id=%s WHERE request_id=%s", (job_id, request_id)
            )
        return request_id

    async def get(self, request_id: str) -> dict[str, Any]:
        await self.open()
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM jf_llm_requests WHERE request_id=%s", (request_id,)
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("LLM request not found")
        return dict(row)

    async def stats(self) -> dict[str, Any]:
        await self.open()
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT profile,state,error AS reason,count(*) AS count,min(created_at) AS oldest,"
                    "min(next_run_at) AS next_run_at,sum(attempts) AS attempts,sum(actual_tokens) AS actual_tokens "
                    "FROM jf_llm_requests GROUP BY profile,state,error ORDER BY profile,state"
                )
            ).fetchall()
            scopes = await (
                await conn.execute(
                    "SELECT scope,not_before,calls,tokens,paused_reason FROM jf_llm_scopes "
                    "ORDER BY scope"
                )
            ).fetchall()
            models = await (
                await conn.execute("SELECT * FROM jf_llm_model_state ORDER BY scope,model")
            ).fetchall()
            runs = await (
                await conn.execute(
                    "SELECT run_id,tenant_id,state,reason,not_before,expires_at,report_request_id FROM jf_llm_runs ORDER BY updated_at DESC LIMIT 50"
                )
            ).fetchall()
        return {
            "requests": [dict(row) for row in rows],
            "scopes": [dict(row) for row in scopes],
            "models": [dict(row) for row in models],
            "runs": [dict(row) for row in runs],
        }
