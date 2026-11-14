"""Optional Procrastinate worker and secret-free queue administration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from tempfile import gettempdir
from time import monotonic
from typing import Any

import httpx
import procrastinate
import structlog
from psycopg.types.json import Jsonb

from job_ftch.application.tenant_locks import tenant_run_lock
from job_ftch.config import Settings, get_settings
from job_ftch.infrastructure.llm import dispatch_metrics
from job_ftch.infrastructure.llm.dispatch_policy import (
    DispatchSignal,
    classify_response,
    retry_time,
)
from job_ftch.infrastructure.llm.dispatch_store import (
    DispatchStore,
    profile_revision,
    route_revision,
)

logger = structlog.get_logger(__name__)


class DispatchWait(Exception):
    def __init__(self, retry_at: datetime) -> None:
        super().__init__("LLM dispatch deferred")
        self.retry_at = retry_at


class DispatchRetry(procrastinate.BaseRetryStrategy):
    def get_retry_decision(self, *, exception: BaseException, job: Any) -> Any:
        del job
        if isinstance(exception, DispatchWait):
            return procrastinate.RetryDecision(retry_at=exception.retry_at)
        return None


class LLMWorker:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.store = DispatchStore(settings)
        self.transport = transport
        self._clients: dict[float, httpx.AsyncClient] = {}
        self.random = random.Random()
        self.store.app.task(name="job_ftch.llm.execute", queue="llm", retry=DispatchRetry())(
            self.execute_job
        )

    def client(self, timeout: float) -> httpx.AsyncClient:
        if timeout not in self._clients:
            self._clients[timeout] = httpx.AsyncClient(
                timeout=timeout,
                transport=self.transport,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=8, max_keepalive_connections=2, keepalive_expiry=20
                ),
            )
        return self._clients[timeout]

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        await self.store.close()

    async def execute_job(self, request_id: str) -> None:
        try:
            await self.execute(request_id)
        except DispatchWait:
            raise
        except Exception:
            row = await self.store.get(request_id)
            await self._finish(
                request_id,
                "uncertain" if row["state"] == "running" else "failed",
                error="dispatch_failure",
            )
            raise RuntimeError("LLM dispatch failed; inspect request state") from None

    async def _finish(
        self,
        request_id: str,
        state: str,
        *,
        response: httpx.Response | None = None,
        error: str | None = None,
        reserved_until: datetime | None = None,
    ) -> None:
        request_row = await self.store.get(request_id)
        request_profile = self.settings.llm_provider_profiles.get(request_row["profile"])
        payload = None
        actual = None
        headers: dict[str, str] = {}
        status = None
        if response is not None:
            status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": {"message": "Upstream returned a non-JSON response"}}
                status = 502
                if request_profile and request_profile.cliproxy_dispatch:
                    state, error = "failed", "invalid_upstream_response"
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "retry-after"}
            }
        async with self.store.pool.connection() as conn, conn.transaction():
            if state == "uncertain":
                await conn.execute(
                    "UPDATE jf_llm_scopes s SET paused_reason='uncertain' "
                    "FROM jf_llm_requests r WHERE r.request_id=%s AND s.scope=r.scope",
                    (request_id,),
                )
            if response is not None and response.is_success and isinstance(payload, dict):
                usage = payload.get("usage") or {}
                actual = usage.get("total_tokens") if isinstance(usage, dict) else None
                if isinstance(actual, int) and actual >= 0:
                    await conn.execute(
                        "UPDATE jf_llm_scopes s SET tokens=GREATEST(0,s.tokens-r.reserved_tokens)+%s "
                        "FROM jf_llm_requests r WHERE r.request_id=%s AND s.scope=r.scope "
                        "AND r.state='running' AND s.window_start <= r.updated_at",
                        (actual, request_id),
                    )
                    if reserved_until is not None and request_profile:
                        policy = self.settings.llm_dispatch_policies[
                            request_profile.quota_scope or request_row["profile"]
                        ]
                        spacing = (
                            max(
                                policy.min_interval_seconds,
                                3600 / policy.max_calls_per_hour,
                                3600 * actual / policy.max_tokens_per_hour,
                            )
                            + policy.jitter_seconds
                        )
                        await conn.execute(
                            "UPDATE jf_llm_scopes SET not_before=GREATEST(now(),%s) "
                            "WHERE scope=%s AND not_before=%s",
                            (
                                request_row["updated_at"] + timedelta(seconds=spacing),
                                request_row["scope"],
                                reserved_until,
                            ),
                        )
            await conn.execute(
                "UPDATE jf_llm_requests SET state=%s,response=%s,response_status=%s,"
                "response_headers=%s,error=%s,actual_tokens=%s,updated_at=now() WHERE request_id=%s",
                (
                    state,
                    Jsonb(payload),
                    status,
                    Jsonb(headers),
                    error,
                    actual if isinstance(actual, int) else None,
                    request_id,
                ),
            )
        row = await self.store.get(request_id)
        profile = self.settings.llm_provider_profiles.get(row["profile"])
        if profile and profile.cliproxy_dispatch:
            dispatch_metrics.outcomes.add(1, {"state": state, "reason": error or "response"})
            if state in {"uncertain", "failed"} or error in {
                "auth",
                "verification_required",
                "billing_required",
            }:
                logger.warning(
                    "cliproxy_operator_required", request_id=request_id, reason=error or state
                )

    async def execute(self, request_id: str) -> None:
        row = await self.store.get(request_id)
        if row["state"] != "pending":
            return
        now = datetime.now(UTC)
        profile = self.settings.llm_provider_profiles.get(row["profile"])
        if profile is None or profile.execution != "queued":
            await self._finish(request_id, "failed", error="profile_disabled")
            return
        scope = profile.quota_scope or row["profile"]
        policy = self.settings.llm_dispatch_policies[scope]
        protected = profile.cliproxy_dispatch
        if row["revision"] != profile_revision(self.settings, row["profile"]):
            await self._finish(request_id, "failed", error="profile_revision_changed")
            return
        if row["expires_at"] <= now or row["attempts"] >= policy.max_attempts:
            await self._finish(request_id, "failed", error="request_expired_or_exhausted")
            return
        if protected and row["logical_run_id"]:
            from job_ftch.infrastructure.llm.dispatch_store import route_revision

            async with self.store.pool.connection() as conn:
                run = await (
                    await conn.execute(
                        "SELECT routes,state,expires_at FROM jf_llm_runs WHERE run_id=%s AND tenant_id=%s",
                        (row["logical_run_id"], row["tenant_id"]),
                    )
                ).fetchone()
            if (
                run is None
                or run["state"] == "operator"
                or run["expires_at"] <= now
                or json.loads(run["routes"]).get(row["profile"])
                != route_revision(self.settings, row["profile"])
            ):
                await self._finish(request_id, "failed", error="run_route_requires_operator")
                return
        payload = row["payload"]
        if profile.egress_ready_url:
            try:
                async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                    ready = (await client.get(profile.egress_ready_url)).status_code == 200
            except httpx.HTTPError:
                ready = False
            if not ready:
                await self._defer(
                    row,
                    min(now + timedelta(seconds=30), row["expires_at"]),
                    reason="egress_offline" if protected else None,
                )
                return
        allowed = set(profile.models) | {
            binding.model
            for binding in self.settings.llm_bindings.values()
            if binding.provider == row["profile"]
        }
        if payload.get("model") not in allowed:
            await self._finish(request_id, "failed", error="model_not_allowed")
            return
        credential = os.environ.get(profile.credential_ref, "")
        if not credential and profile.credential_ref == "JOB_FTCH_OPENAI_API_KEY":
            credential = (
                self.settings.openai_api_key.get_secret_value()
                if self.settings.openai_api_key
                else ""
            )
        if not credential:
            await self._finish(request_id, "failed", error="missing_credential")
            return
        estimate = len(json.dumps(payload, ensure_ascii=False).encode()) + int(
            payload.get("max_completion_tokens") or payload.get("max_tokens") or 4096
        )
        if estimate > policy.max_tokens_per_hour:
            await self._finish(request_id, "failed", error="request_exceeds_token_budget")
            return
        async with self.store.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (scope,))
            await conn.execute(
                "INSERT INTO jf_llm_scopes(scope) VALUES (%s) ON CONFLICT DO NOTHING", (scope,)
            )
            quota = await (
                await conn.execute(
                    "SELECT * FROM jf_llm_scopes WHERE scope=%s FOR UPDATE", (scope,)
                )
            ).fetchone()
            assert quota is not None
            model_state = None
            if protected:
                await conn.execute(
                    "INSERT INTO jf_llm_model_state(scope,model) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (scope, payload["model"]),
                )
                model_state = await (
                    await conn.execute(
                        "SELECT * FROM jf_llm_model_state WHERE scope=%s AND model=%s FOR UPDATE",
                        (scope, payload["model"]),
                    )
                ).fetchone()
            now = datetime.now(UTC)
            # Procrastinate's account lock serializes network calls; this transaction
            # reserves shared time and budgets before any upstream request.
            if quota["window_start"] + timedelta(hours=1) <= now:
                quota.update(window_start=now, calls=0, tokens=0)
            wait_until = max(now, quota["not_before"], row["next_run_at"])
            wait_reason = "admission"
            if model_state and model_state["not_before"] > wait_until:
                wait_until = model_state["not_before"]
                wait_reason = model_state["reason"] or "provider_unavailable"
            if (
                quota["calls"] >= policy.max_calls_per_hour
                or quota["tokens"] + estimate > policy.max_tokens_per_hour
            ):
                wait_until = max(wait_until, quota["window_start"] + timedelta(hours=1))
                wait_reason = "local_budget"
            if quota["paused_reason"]:
                wait_until = max(wait_until, now + timedelta(seconds=60))
                wait_reason = quota["paused_reason"]
            if wait_until > now:
                await conn.execute(
                    "UPDATE jf_llm_requests SET next_run_at=%s,error=%s,updated_at=now() WHERE request_id=%s",
                    (wait_until, wait_reason if protected else row["error"], request_id),
                )
            else:
                spacing = policy.min_interval_seconds
                if protected:
                    spacing = max(
                        spacing,
                        3600 / policy.max_calls_per_hour,
                        3600 * estimate / policy.max_tokens_per_hour,
                    ) + self.random.uniform(0, policy.jitter_seconds)
                reserved_until = now + timedelta(seconds=spacing)
                await conn.execute(
                    "UPDATE jf_llm_scopes SET window_start=%s,calls=%s,tokens=%s,not_before=%s "
                    "WHERE scope=%s",
                    (
                        quota["window_start"],
                        quota["calls"] + 1,
                        quota["tokens"] + estimate,
                        reserved_until,
                        scope,
                    ),
                )
                await conn.execute(
                    "UPDATE jf_llm_requests SET state='running',attempts=attempts+1,"
                    "reserved_tokens=%s,updated_at=now() WHERE request_id=%s",
                    (estimate, request_id),
                )
        if wait_until > now:
            if protected:
                dispatch_metrics.waits.add(1, {"reason": wait_reason})
            raise DispatchWait(min(wait_until, row["expires_at"]))
        base_url = profile.base_url or self.settings.openai_base_url or "https://api.openai.com/v1"
        headers = {"Authorization": f"Bearer {credential}"}
        if profile.session_affinity or protected:
            headers["X-Session-ID"] = row["session_id"]
        started = monotonic()
        if protected:
            dispatch_metrics.attempts.add(1, {"operation": row["operation"]})
        try:
            if protected:
                async with self.client(profile.timeout_seconds).stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as remote:
                    content = bytearray()
                    async for chunk in remote.aiter_bytes(65536):
                        content.extend(chunk)
                        if len(content) > 4_000_000:
                            await self._finish(request_id, "failed", error="response_too_large")
                            return
                    response = httpx.Response(
                        remote.status_code,
                        headers=remote.headers,
                        content=bytes(content),
                        request=remote.request,
                    )
            else:
                async with httpx.AsyncClient(
                    timeout=profile.timeout_seconds, transport=self.transport, trust_env=False
                ) as client:
                    response = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if protected:
                signal = DispatchSignal(
                    "provider_unavailable",
                    datetime.now(UTC) + timedelta(seconds=policy.retry_delay_seconds),
                )
                due = await self.record_signal(row, signal)
                await self._defer(row, due, reason=signal.reason)
                return
            await self._defer(row, now + timedelta(seconds=policy.retry_delay_seconds))
            return
        except httpx.HTTPError as exc:
            if protected:
                logger.warning(
                    "cliproxy_upstream_outcome_unknown",
                    request_id=request_id,
                    error_type=type(exc).__name__,
                )
            await self._finish(request_id, "uncertain", error="upstream_outcome_unknown")
            return
        finally:
            if protected:
                dispatch_metrics.latency.record(monotonic() - started)
        if len(response.content) > 4_000_000:
            await self._finish(request_id, "failed", error="response_too_large")
            return
        if protected:
            signal = classify_response(
                response,
                now=datetime.now(UTC),
                default_seconds=policy.retry_delay_seconds * (2 ** row["attempts"]),
            )
            if signal.operator:
                async with self.store.pool.connection() as conn:
                    await conn.execute(
                        "UPDATE jf_llm_scopes SET paused_reason=%s WHERE scope=%s",
                        (signal.reason, scope),
                    )
                await self._finish(request_id, "failed", response=response, error=signal.reason)
                return
            if signal.retry_at is not None:
                due = await self.record_signal(row, signal)
                if row["attempts"] + 1 < policy.max_attempts and due < row["expires_at"]:
                    await self._defer(row, due, reason=signal.reason)
                    return
                await self._finish(request_id, "failed", response=response, error=signal.reason)
                return
            if response.is_success:
                async with self.store.pool.connection() as conn:
                    prior = await (
                        await conn.execute(
                            "UPDATE jf_llm_model_state SET failures=0,circuit_open=false,not_before=now(),reason=NULL WHERE scope=%s AND model=%s RETURNING *",
                            (scope, payload["model"]),
                        )
                    ).fetchone()
                if model_state and model_state["circuit_open"] and prior:
                    dispatch_metrics.transitions.add(1, {"state": "closed"})
                    logger.info("cliproxy_circuit_closed", model=payload["model"])
            await self._finish(
                request_id,
                "succeeded" if response.is_success else "failed",
                response=response,
                error=None if response.is_success else signal.reason,
                reserved_until=reserved_until,
            )
            return
        if response.status_code in {401, 403}:
            async with self.store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE jf_llm_scopes SET paused_reason='auth' WHERE scope=%s", (scope,)
                )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            due = retry_time(
                response.headers.get("retry-after"),
                now=datetime.now(UTC),
                default_seconds=policy.retry_delay_seconds * (2 ** row["attempts"]),
            )
            try:
                error_text = json.dumps(response.json()).casefold()
            except ValueError:
                error_text = ""
            if "insufficient_quota" in error_text or "resource_exhausted" in error_text:
                due = max(due, datetime.now(UTC) + timedelta(minutes=30))
            async with self.store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE jf_llm_scopes SET not_before=GREATEST(not_before,%s) WHERE scope=%s",
                    (due, scope),
                )
            if row["attempts"] + 1 < policy.max_attempts and due < row["expires_at"]:
                await self._defer(row, due)
                return
        await self._finish(
            request_id, "succeeded" if response.is_success else "failed", response=response
        )

    async def record_signal(self, row: dict[str, Any], signal: DispatchSignal) -> datetime:
        policy = self.settings.llm_dispatch_policies[row["scope"]]
        due = signal.retry_at or datetime.now(UTC) + timedelta(seconds=policy.retry_delay_seconds)
        due += timedelta(seconds=self.random.uniform(0, policy.jitter_seconds))
        async with self.store.pool.connection() as conn, conn.transaction():
            model = await (
                await conn.execute(
                    "SELECT * FROM jf_llm_model_state WHERE scope=%s AND model=%s FOR UPDATE",
                    (row["scope"], row["payload"]["model"]),
                )
            ).fetchone()
            assert model is not None
            failures = model["failures"]
            circuit_open = model["circuit_open"]
            if signal.reason in {"provider_unavailable", "model_capacity"}:
                if (
                    not circuit_open
                    and model["last_failure_at"]
                    and model["last_failure_at"] < datetime.now(UTC) - timedelta(minutes=5)
                ):
                    failures = 0
                failures += 1
                circuit_open = failures >= policy.circuit_failure_threshold
                if circuit_open:
                    duration = min(
                        policy.circuit_max_cooldown_seconds,
                        policy.circuit_cooldown_seconds
                        * 2 ** min(failures - policy.circuit_failure_threshold, 10),
                    )
                    due = max(due, datetime.now(UTC) + timedelta(seconds=duration))
                    if not model["circuit_open"]:
                        dispatch_metrics.transitions.add(1, {"state": "open"})
                        logger.warning(
                            "cliproxy_circuit_open",
                            model=row["payload"]["model"],
                            retry_at=due.isoformat(),
                        )
            await conn.execute(
                "UPDATE jf_llm_model_state SET not_before=GREATEST(not_before,%s),failures=%s,circuit_open=%s,reason=%s,last_failure_at=now() WHERE scope=%s AND model=%s",
                (due, failures, circuit_open, signal.reason, row["scope"], row["payload"]["model"]),
            )
            if signal.reason == "rate_limit":
                await conn.execute(
                    "UPDATE jf_llm_scopes SET not_before=GREATEST(not_before,%s) WHERE scope=%s",
                    (due, row["scope"]),
                )
        return due

    async def _defer(
        self, row: dict[str, Any], due: datetime, *, reason: str | None = None
    ) -> None:
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "UPDATE jf_llm_requests SET state='pending',next_run_at=%s,error=%s,updated_at=now() "
                "WHERE request_id=%s",
                (due, reason, row["request_id"]),
            )
        if reason:
            dispatch_metrics.waits.add(1, {"reason": reason})
        raise DispatchWait(due)

    async def maintain(self) -> None:
        """Reconcile dead jobs conservatively; never replay an unknown HTTP outcome."""
        if any(
            profile.cliproxy_dispatch for profile in self.settings.llm_provider_profiles.values()
        ):
            from job_ftch.infrastructure.llm.dispatch_reports import enqueue_run_reports

            await enqueue_run_reports(self.store)
            async with self.store.pool.connection() as conn:
                oldest = await (
                    await conn.execute(
                        "SELECT extract(epoch from now()-min(created_at)) AS age FROM jf_llm_requests WHERE state='pending' AND profile=ANY(%s)",
                        (
                            [
                                name
                                for name, p in self.settings.llm_provider_profiles.items()
                                if p.cliproxy_dispatch
                            ],
                        ),
                    )
                ).fetchone()
            if oldest and oldest["age"] is not None:
                dispatch_metrics.queue_age.record(float(oldest["age"]))
        for job in await self.store.app.job_manager.get_stalled_jobs():
            if job.task_name != "job_ftch.llm.execute":
                continue
            request_id = job.task_kwargs.get("request_id")
            if isinstance(request_id, str):
                row = await self.store.get(request_id)
                if row["state"] == "running":
                    await self._finish(request_id, "uncertain", error="worker_lost_during_request")
            await self.store.app.job_manager.retry_job(job)
        async with self.store.pool.connection() as conn:
            lost = await (
                await conn.execute(
                    "SELECT r.request_id FROM jf_llm_requests r LEFT JOIN procrastinate_jobs j "
                    "ON r.job_id=j.id WHERE r.state='running' "
                    "AND (j.id IS NULL OR j.status::text IN ('failed','aborted','succeeded'))"
                )
            ).fetchall()
        for row in lost:
            await self._finish(row["request_id"], "uncertain", error="broker_outcome_unknown")
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "UPDATE jf_llm_requests SET state='failed',error='expired',updated_at=now() "
                "WHERE state='pending' AND expires_at < now()"
            )
            retention = max(
                (p.retention_seconds for p in self.settings.llm_dispatch_policies.values()),
                default=86400,
            )
            await conn.execute(
                "DELETE FROM jf_llm_requests r WHERE state IN ('succeeded','failed','cancelled') "
                "AND updated_at < now() - (%s * interval '1 second') "
                "AND NOT EXISTS(SELECT 1 FROM jf_llm_runs u WHERE u.run_id=r.logical_run_id AND u.state IN ('active','waiting','operator'))",
                (retention,),
            )
            await conn.execute(
                "DELETE FROM jf_llm_runs WHERE state='completed' AND updated_at < now()-(%s*interval '1 second') AND (report_request_id LIKE 'skipped:%%' OR NOT EXISTS(SELECT 1 FROM jf_llm_requests r WHERE r.request_id=report_request_id AND r.state IN ('pending','running','uncertain')))",
                (retention,),
            )
        await self.store.app.job_manager.delete_old_jobs(
            nb_hours=max(1, ceil(retention / 3600)),
            queue="llm",
            include_failed=True,
            include_cancelled=True,
            include_aborted=True,
        )

    async def run(self) -> None:
        if any(p.cliproxy_dispatch for p in self.settings.llm_provider_profiles.values()):
            from job_ftch.application.logging import configure_logging
            from job_ftch.infrastructure.observability import configure_observability

            configure_logging(self.settings.log_level)
            configure_observability(self.settings)
        await self.store.open()

        async def maintenance() -> None:
            while True:
                await self.maintain()
                (Path(gettempdir()) / "job_ftch_llm_worker_ready").touch()
                await asyncio.sleep(30)

        task = asyncio.create_task(maintenance())
        try:
            await self.store.app.run_worker_async(queues=["llm"], concurrency=4)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.close()


async def recover_run(store: DispatchStore, settings: Settings, run_id: str) -> tuple[str, int]:
    if settings.tenant_lock_dir is None:
        raise ValueError("Recovery requires the bot's shared tenant lock directory")
    await store.open()
    async with store.pool.connection() as conn:
        tenant = await (
            await conn.execute("SELECT tenant_id FROM jf_llm_runs WHERE run_id=%s", (run_id,))
        ).fetchone()
    if tenant is None:
        raise ValueError("Unknown run ID")
    async with (
        tenant_run_lock(settings, tenant["tenant_id"], timeout=0),
        store.pool.connection() as conn,
        conn.transaction(),
    ):
        run = await (
            await conn.execute("SELECT * FROM jf_llm_runs WHERE run_id=%s FOR UPDATE", (run_id,))
        ).fetchone()
        if run is None or run["state"] != "operator" or run["reason"] != "operator":
            raise ValueError("Run is not held for operator review")
        routes = json.dumps(
            {
                name: route_revision(settings, name)
                for name in sorted(
                    {
                        binding.provider
                        for binding in settings.llm_bindings.values()
                        if settings.llm_provider_profiles[binding.provider].cliproxy_dispatch
                    }
                )
            },
            sort_keys=True,
        )
        if run["routes"] != routes:
            raise ValueError("Run route changed; review credentials and account generation")
        siblings = await (
            await conn.execute(
                "SELECT count(*) AS n FROM jf_llm_runs WHERE tenant_id=%s AND selector=%s "
                "AND run_id<>%s AND state IN ('active','waiting','operator')",
                (run["tenant_id"], run["selector"], run_id),
            )
        ).fetchone()
        if siblings and siblings["n"]:
            raise ValueError("Another logical run is active for this selector")
        blocked = await (
            await conn.execute(
                "SELECT count(*) AS n FROM jf_llm_requests WHERE logical_run_id=%s "
                "AND (state IN ('pending','running','cancelled') OR "
                "(state='failed' AND coalesce(error,'')<>'run_route_requires_operator'))",
                (run_id,),
            )
        ).fetchone()
        if blocked and blocked["n"]:
            raise ValueError("Resolve other outstanding requests first")
        unknown = await (
            await conn.execute(
                "SELECT request_id,scope,error,profile FROM jf_llm_requests "
                "WHERE logical_run_id=%s AND state='uncertain' FOR UPDATE",
                (run_id,),
            )
        ).fetchall()
        if len(unknown) != 1 or unknown[0]["error"] != "upstream_outcome_unknown":
            raise ValueError("Expected exactly one reviewed unknown upstream outcome")
        request = unknown[0]
        profile = settings.llm_provider_profiles.get(request["profile"])
        if not profile or not profile.cliproxy_dispatch:
            raise ValueError("Recovery is only available for protected CLIProxy requests")
        scope = await (
            await conn.execute(
                "SELECT paused_reason FROM jf_llm_scopes WHERE scope=%s FOR UPDATE",
                (request["scope"],),
            )
        ).fetchone()
        other_unknown = await (
            await conn.execute(
                "SELECT count(*) AS n FROM jf_llm_requests WHERE scope=%s AND state='uncertain'",
                (request["scope"],),
            )
        ).fetchone()
        if (
            not scope
            or scope["paused_reason"] != "uncertain"
            or not other_unknown
            or other_unknown["n"] != 1
        ):
            raise ValueError("Account pause or unknown-outcome count changed")
        old_key = f"{run['tenant_id']}:llm_continuation:{run_id}"
        checkpoint = await (
            await conn.execute("SELECT value FROM jf_kv WHERE key=%s FOR UPDATE", (old_key,))
        ).fetchone()
        if checkpoint is None:
            raise ValueError("Observation checkpoint is missing")
        references = json.loads(checkpoint["value"])
        if not isinstance(references, list) or any(
            not isinstance(ref, list)
            or len(ref) != 2
            or not all(isinstance(part, str) and part for part in ref)
            for ref in references
        ):
            raise ValueError("Invalid observation checkpoint")
        ttl = min(
            settings.llm_dispatch_policies[
                settings.llm_provider_profiles[name].quota_scope or name
            ].request_ttl_seconds
            for name in json.loads(routes)
        )
        replacement = uuid.uuid4().hex
        await conn.execute(
            "UPDATE jf_llm_requests SET state='cancelled',updated_at=now() WHERE request_id=%s",
            (request["request_id"],),
        )
        await conn.execute(
            "UPDATE jf_llm_runs SET state='completed',"
            "report_request_id=coalesce(report_request_id,'skipped:operator_release'),"
            "updated_at=now() WHERE run_id=%s",
            (run_id,),
        )
        await conn.execute(
            "INSERT INTO jf_llm_runs(run_id,tenant_id,selector,routes,state,not_before,"
            "expires_at,summary) VALUES (%s,%s,%s,%s,'waiting',now(),%s,%s)",
            (
                replacement,
                run["tenant_id"],
                run["selector"],
                run["routes"],
                datetime.now(UTC) + timedelta(seconds=ttl),
                Jsonb({"completion_state": "waiting_llm"}),
            ),
        )
        await conn.execute(
            "INSERT INTO jf_kv(key,value,updated_at) VALUES (%s,%s,now())",
            (f"{run['tenant_id']}:llm_continuation:{replacement}", checkpoint["value"]),
        )
        await conn.execute(
            "INSERT INTO jf_kv(key,value,updated_at) SELECT %s,value,now() FROM jf_kv WHERE key=%s",
            (
                f"{run['tenant_id']}:llm_acquisition:{replacement}",
                f"{run['tenant_id']}:llm_acquisition:{run_id}",
            ),
        )
        await conn.execute(
            "UPDATE jf_llm_scopes SET paused_reason=NULL WHERE scope=%s",
            (request["scope"],),
        )
        return replacement, len(references)


async def admin(args: argparse.Namespace) -> None:
    worker = LLMWorker(get_settings())
    store = worker.store
    try:
        if args.command == "init":
            await store.initialize()
            print("LLM queue schema ready")
        elif args.command == "worker":
            await worker.run()
        elif args.command == "status":
            print(json.dumps(await store.stats(), default=str))
        elif args.command == "report":
            await store.open()
            async with store.pool.connection() as conn:
                row = await (
                    await conn.execute(
                        "SELECT u.run_id,u.state,u.summary,r.state AS report_state,r.response FROM jf_llm_runs u LEFT JOIN jf_llm_requests r ON r.request_id=u.report_request_id WHERE u.run_id=%s",
                        (args.target,),
                    )
                ).fetchone()
            if row is None:
                raise ValueError("Unknown run ID")
            response = row.pop("response") or {}
            choices = response.get("choices") or []
            row["analysis"] = (
                str(choices[0].get("message", {}).get("content", ""))[:8000] if choices else None
            )
            print(json.dumps(row, default=str, ensure_ascii=False))
        elif args.command == "release-run":
            await store.open()
            async with store.pool.connection() as conn, conn.transaction():
                locked = await (
                    await conn.execute(
                        "SELECT run_id FROM jf_llm_runs WHERE run_id=%s FOR UPDATE", (args.target,)
                    )
                ).fetchone()
                if locked is None:
                    raise ValueError("Unknown run ID")
                pending = await (
                    await conn.execute(
                        "SELECT count(*) AS n FROM jf_llm_requests WHERE logical_run_id=%s AND state IN ('pending','running','uncertain')",
                        (args.target,),
                    )
                ).fetchone()
                if pending and pending["n"]:
                    raise ValueError(
                        "Resolve or explicitly cancel outstanding requests before releasing the run"
                    )
                await conn.execute(
                    "UPDATE jf_llm_runs SET state='completed',report_request_id=coalesce(report_request_id,'skipped:operator_release'),updated_at=now() WHERE run_id=%s",
                    (args.target,),
                )
            print("Run released after operator review; account pause is unchanged")
        elif args.command == "recover-run":
            if not args.reviewed_unknown:
                raise ValueError("Review the unknown upstream outcome before recovery")
            replacement, references = await recover_run(store, worker.settings, args.target)
            print(json.dumps({"new_run_id": replacement, "checkpoint_references": references}))
        else:
            await store.open()
            async with store.pool.connection() as conn:
                if args.command in {"pause", "resume"}:
                    protected = any(
                        p.cliproxy_dispatch and (p.quota_scope or name) == args.target
                        for name, p in worker.settings.llm_provider_profiles.items()
                    )
                    if args.command == "resume" and protected:
                        unknown = await (
                            await conn.execute(
                                "SELECT count(*) AS n FROM jf_llm_requests WHERE scope=%s AND state='uncertain'",
                                (args.target,),
                            )
                        ).fetchone()
                        if unknown and unknown["n"]:
                            raise ValueError(
                                "Resolve and explicitly cancel unknown outcomes before resuming the account"
                            )
                    await conn.execute(
                        "INSERT INTO jf_llm_scopes(scope,paused_reason) VALUES (%s,%s) "
                        "ON CONFLICT(scope) DO UPDATE SET paused_reason=EXCLUDED.paused_reason",
                        (args.target, "operator" if args.command == "pause" else None),
                    )
                else:
                    result = await conn.execute(
                        "UPDATE jf_llm_requests SET state='cancelled',updated_at=now() "
                        "WHERE request_id=%s AND state IN ('pending','uncertain','failed')",
                        (args.target,),
                    )
                    print(f"Cancelled requests: {result.rowcount}")
    finally:
        await worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "init",
            "worker",
            "status",
            "pause",
            "resume",
            "cancel",
            "report",
            "release-run",
            "recover-run",
        ],
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("--reviewed-unknown", action="store_true")
    args = parser.parse_args()
    if (
        args.command in {"pause", "resume", "cancel", "report", "release-run", "recover-run"}
        and not args.target
    ):
        parser.error("target scope/request ID is required")
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(admin(args))
    else:
        asyncio.run(admin(args))


if __name__ == "__main__":
    main()
