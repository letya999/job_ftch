"""Durable CLIProxy logical runs; item prompts never share conversation history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from psycopg import InterfaceError, OperationalError
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from job_ftch.application.llm_dispatch import LLMRequestDeferredError, llm_run
from job_ftch.infrastructure.llm.dispatch_store import (
    RETRYABLE_TERMINAL_ERRORS,
    DispatchStore,
    route_revision,
)

UPSTREAM_RETRYABLE_ERRORS = RETRYABLE_TERMINAL_ERRORS - {"run_route_requires_operator"}

if TYPE_CHECKING:
    from job_ftch.config import Settings


class CliproxyRun:
    def __init__(
        self,
        settings: Settings,
        tenant: str,
        *,
        selector: str = "default",
        trigger: str = "manual",
        attempt_run_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.tenant = tenant
        self.selector = selector
        self.trigger = trigger
        self.attempt_run_id = attempt_run_id
        self.parent_run_id = parent_run_id
        self.store = DispatchStore(settings)
        self.run_id = ""
        self._token: Any = None

    async def __aenter__(self) -> CliproxyRun:
        profiles = {
            binding.provider: self.settings.llm_provider_profiles[binding.provider]
            for binding in self.settings.llm_bindings.values()
            if self.settings.llm_provider_profiles[binding.provider].cliproxy_dispatch
        }
        routes = json.dumps(
            {name: route_revision(self.settings, name) for name in sorted(profiles)}, sort_keys=True
        )
        policies = [
            self.settings.llm_dispatch_policies[p.quota_scope or name]
            for name, p in profiles.items()
        ]
        now = datetime.now(UTC)
        try:
            await self.store.open()
            async with self.store.pool.connection() as conn, conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"run:{self.tenant}:{self.selector}",),
                )
                row = await (
                    await conn.execute(
                        "SELECT * FROM jf_llm_runs WHERE tenant_id=%s "
                        "AND state IN ('active','waiting','operator') "
                        "AND (selector=%s OR %s=ANY(source_run_ids)) "
                        "ORDER BY CASE WHEN %s=ANY(source_run_ids) THEN 0 ELSE 1 END,created_at DESC LIMIT 1 FOR UPDATE",
                        (self.tenant, self.selector, self.parent_run_id, self.parent_run_id),
                    )
                ).fetchone()
                if (
                    row is not None
                    and row["routes"] == routes
                    and (
                        row["state"] in {"active", "waiting"}
                        or (row["state"] == "operator" and row["reason"] == "run_expired")
                    )
                    and isinstance(row["summary"], dict)
                    and str(row["summary"].get("completion_state", "")).startswith("waiting")
                ):
                    blockers = await (
                        await conn.execute(
                            "SELECT count(*) AS n FROM jf_llm_requests WHERE logical_run_id=%s "
                            "AND (state IN ('uncertain','cancelled') OR "
                            "(state='failed' AND NOT (COALESCE(error,'')=ANY(%s))))",
                            (row["run_id"], list(RETRYABLE_TERMINAL_ERRORS)),
                        )
                    ).fetchone()
                    if blockers and not blockers["n"]:
                        expiry = now + timedelta(
                            seconds=min(p.request_ttl_seconds for p in policies)
                        )
                        await conn.execute(
                            "UPDATE jf_llm_runs SET state='waiting',expires_at=%s,"
                            "reason=CASE WHEN reason='run_expired' THEN 'lease_renewed' "
                            "ELSE reason END,updated_at=now() WHERE run_id=%s",
                            (expiry, row["run_id"]),
                        )
                        row["state"] = "waiting"
                        row["expires_at"] = expiry
                if (
                    row is not None
                    and row["state"] == "operator"
                    and row["routes"] == routes
                    and row["expires_at"] > now
                    and row["reason"]
                    not in {"unexpected_exception", "route_changed", "run_expired"}
                    and isinstance(row["summary"], dict)
                    and str(row["summary"].get("completion_state", "")).startswith("waiting")
                ):
                    failures = await (
                        await conn.execute(
                            "SELECT count(*) FILTER (WHERE state IN "
                            "('pending','running','uncertain','cancelled') OR "
                            "(state='failed' AND NOT (COALESCE(error,'')=ANY(%s)))) AS blocking, "
                            "bool_or(state='failed' AND error=ANY(%s)) AS retryable "
                            "FROM jf_llm_requests WHERE logical_run_id=%s",
                            (
                                list(RETRYABLE_TERMINAL_ERRORS),
                                list(UPSTREAM_RETRYABLE_ERRORS),
                                row["run_id"],
                            ),
                        )
                    ).fetchone()
                    if failures and failures["retryable"] and not failures["blocking"]:
                        await conn.execute(
                            "UPDATE jf_llm_runs SET state='waiting',reason='retryable_failure',"
                            "updated_at=now() WHERE run_id=%s",
                            (row["run_id"],),
                        )
                        row["state"] = "waiting"
                if row is None:
                    self.run_id = uuid.uuid4().hex
                    delay = (
                        random.uniform(0, max(p.run_jitter_seconds for p in policies))
                        if self.trigger == "schedule"
                        else 0
                    )
                    due = now + timedelta(seconds=delay)
                    expiry = now + timedelta(seconds=min(p.request_ttl_seconds for p in policies))
                    await conn.execute(
                        "INSERT INTO jf_llm_runs(run_id,tenant_id,selector,routes,not_before,expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (self.run_id, self.tenant, self.selector, routes, due, expiry),
                    )
                    row = {
                        "run_id": self.run_id,
                        "routes": routes,
                        "state": "active",
                        "not_before": due,
                        "expires_at": expiry,
                    }
                self.run_id = row["run_id"]
                if self.attempt_run_id:
                    await conn.execute(
                        "UPDATE jf_llm_runs SET source_run_ids=source_run_ids || %s::text[] WHERE run_id=%s AND NOT (%s=ANY(source_run_ids))",
                        ([self.attempt_run_id], self.run_id, self.attempt_run_id),
                    )
                reason = (
                    "route_changed"
                    if row["routes"] != routes
                    else "run_expired"
                    if row["expires_at"] <= now
                    else "operator"
                    if row["state"] in {"operator", "completed"}
                    else ""
                )
                if reason:
                    await conn.execute(
                        "UPDATE jf_llm_runs SET state='operator',reason=%s,updated_at=now() WHERE run_id=%s",
                        (reason, self.run_id),
                    )
                due = row["not_before"]
            if reason:
                raise LLMRequestDeferredError(
                    self.run_id, now + timedelta(minutes=5), reason=reason, requires_operator=True
                )
            if due > now:
                raise LLMRequestDeferredError(self.run_id, due, reason="scheduled_start")
            self._token = llm_run.set(self.run_id)
            return self
        except (OperationalError, InterfaceError, PoolTimeout) as exc:
            await self.store.close()
            raise LLMRequestDeferredError(
                self.run_id or self.selector,
                now + timedelta(seconds=30),
                reason="queue_unavailable",
            ) from exc
        except BaseException:
            await self.store.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc is not None and self.run_id:
                async with self.store.pool.connection() as conn:
                    state = (
                        "waiting"
                        if isinstance(exc, (LLMRequestDeferredError, asyncio.CancelledError))
                        else "operator"
                    )
                    await conn.execute(
                        "UPDATE jf_llm_runs SET state=%s,"
                        "reason=CASE WHEN %s='operator' THEN 'unexpected_exception' ELSE reason END,"
                        "updated_at=now() WHERE run_id=%s AND state='active'",
                        (state, state, self.run_id),
                    )
        finally:
            if self._token is not None:
                llm_run.reset(self._token)
            await self.store.close()

    async def finish(self, summary: dict[str, Any]) -> None:
        if summary.get("skipped_already_active"):
            return
        waiting = (
            str(summary.get("completion_state", "")).startswith("waiting")
            or summary.get("completion_state") == "llm_preflight_blocked"
            or bool(summary.get("next_retry_at"))
        )
        counters = {
            key: summary.get(key, 0)
            for key in (
                "fetched",
                "emitted",
                "failed",
                "rejected",
                "deferred",
                "llm_usage_requests",
                "llm_cost_usd",
            )
        }
        counters["completion_state"] = summary.get("completion_state", "completed")
        async with self.store.pool.connection() as conn:
            outstanding = await (
                await conn.execute(
                    "SELECT min(next_run_at) AS due,bool_or(state IN ('uncertain','cancelled') "
                    "OR (state='failed' AND NOT (COALESCE(error,'')=ANY(%s)))) AS operator "
                    "FROM jf_llm_requests WHERE logical_run_id=%s AND operation='work' "
                    "AND state IN ('pending','running','uncertain','cancelled','failed')",
                    (list(RETRYABLE_TERMINAL_ERRORS), self.run_id),
                )
            ).fetchone()
            assert outstanding is not None
            incomplete = bool(outstanding and outstanding["due"])
            state = (
                "operator"
                if incomplete and outstanding["operator"]
                else "waiting"
                if waiting or incomplete
                else "completed"
            )
            if summary.get("llm_health_error") in {
                "provider_request_failed",
                "schema_validation_failed",
                "auth",
                "verification_required",
                "billing_required",
            }:
                state = "operator"
            await conn.execute(
                "UPDATE jf_llm_runs SET state=%s,summary=%s,reason=%s,updated_at=now() "
                "WHERE run_id=%s AND state IN ('active','waiting')",
                (
                    state,
                    Jsonb(counters),
                    str(summary.get("llm_health_error") or "")[:240] or None,
                    self.run_id,
                ),
            )
        if incomplete and not waiting:
            raise LLMRequestDeferredError(
                self.run_id,
                max(outstanding["due"], datetime.now(UTC) + timedelta(seconds=30)),
                reason="unfinished_run",
                requires_operator=bool(outstanding["operator"]),
            )


def run_selector(user_id: str | None, source_ids: Any, personal_mode: bool) -> str:
    value = json.dumps([user_id, sorted(source_ids) if source_ids else None, personal_mode])
    return hashlib.sha256(value.encode()).hexdigest()
