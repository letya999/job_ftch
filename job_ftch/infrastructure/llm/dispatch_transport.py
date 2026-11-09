"""Queue non-streaming compatible HTTP calls without changing Instructor schemas."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any

import httpx
from psycopg import InterfaceError, OperationalError
from psycopg_pool import PoolTimeout
from structlog.contextvars import get_contextvars

from job_ftch.application.llm_dispatch import (
    LLMRequestDeferredError,
    llm_run,
    llm_session,
    run_session_identifier,
    session_identifier,
)
from job_ftch.infrastructure.llm.dispatch_store import DispatchStore

if TYPE_CHECKING:
    from job_ftch.config import Settings


class QueuedLLMTransport(httpx.AsyncBaseTransport):
    def __init__(self, settings: Settings, profile: str, *, ttl_seconds: int | None = None) -> None:
        self.store = DispatchStore(settings)
        self.name = profile
        self.tenant = settings.tenant_id
        config = settings.llm_provider_profiles[profile]
        self.policy = settings.llm_dispatch_policies[config.quota_scope or profile]
        self.ttl_seconds = ttl_seconds
        self.protected = config.cliproxy_dispatch

    @staticmethod
    def deferred_response(error: LLMRequestDeferredError) -> httpx.Response:
        return httpx.Response(
            425,
            json={
                "error": {
                    "code": "llm_deferred",
                    "request_id": error.request_id,
                    "retry_at": error.retry_at.isoformat(),
                    "reason": error.reason,
                    "requires_operator": error.requires_operator,
                    "message": "LLM work preserved for continuation",
                }
            },
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._handle_request(request)
        except (OperationalError, InterfaceError, PoolTimeout):
            if not self.protected:
                raise
            return self.deferred_response(
                LLMRequestDeferredError(
                    self.name,
                    datetime.now(UTC) + timedelta(seconds=30),
                    reason="queue_unavailable",
                )
            )

    async def _handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json={"error": {"message": "Unsupported queued endpoint"}})
        body = await request.aread()
        if len(body) > 2_000_000:
            return httpx.Response(413, json={"error": {"message": "LLM request too large"}})
        payload: dict[str, Any] = json.loads(body)
        if payload.get("stream"):
            return httpx.Response(
                400, json={"error": {"message": "Queued streaming is unsupported"}}
            )
        tenant = str(get_contextvars().get("tenant_id") or self.tenant)
        session = session_identifier(tenant, body)
        run_id = llm_run.get() if self.protected else ""
        if run_id:
            session = run_session_identifier(tenant, run_id)
        preflight = request.headers.get("x-job-ftch-operation") == "preflight"
        if preflight and not self.protected:
            session = "preflight"
        if preflight and self.protected:
            await self.store.open()
            profile = self.store.settings.llm_provider_profiles[self.name]
            async with self.store.pool.connection() as conn:
                availability = await (
                    await conn.execute(
                        "SELECT s.paused_reason,m.not_before,m.reason FROM jf_llm_scopes s "
                        "LEFT JOIN jf_llm_model_state m ON m.scope=s.scope AND m.model=%s WHERE s.scope=%s",
                        (payload.get("model"), profile.quota_scope or self.name),
                    )
                ).fetchone()
            if availability and (
                availability["paused_reason"]
                or (availability["not_before"] and availability["not_before"] > datetime.now(UTC))
            ):
                due = max(
                    availability["not_before"] or datetime.now(UTC),
                    datetime.now(UTC) + timedelta(seconds=30),
                )
                return self.deferred_response(
                    LLMRequestDeferredError(
                        self.name,
                        due,
                        reason=availability["paused_reason"]
                        or availability["reason"]
                        or "cooldown",
                        requires_operator=bool(availability["paused_reason"]),
                    )
                )
        if preflight and self.protected and self.policy.preflight_cache_seconds:
            await self.store.open()
            profile = self.store.settings.llm_provider_profiles[self.name]
            async with self.store.pool.connection() as conn:
                recent = await (
                    await conn.execute(
                        "SELECT r.response FROM jf_llm_requests r JOIN jf_llm_scopes s ON s.scope=r.scope "
                        "LEFT JOIN jf_llm_model_state m ON m.scope=r.scope AND m.model=r.payload->>'model' "
                        "WHERE r.scope=%s AND r.tenant_id=%s AND r.payload->>'model'=%s AND r.state='succeeded' "
                        "AND s.paused_reason IS NULL AND (m.not_before IS NULL OR m.not_before<=now()) "
                        "AND r.updated_at > now()-(%s*interval '1 second') ORDER BY r.updated_at DESC LIMIT 1",
                        (
                            profile.quota_scope or self.name,
                            tenant,
                            payload.get("model"),
                            self.policy.preflight_cache_seconds,
                        ),
                    )
                ).fetchone()
            if recent and isinstance(recent["response"], dict):
                cached = dict(recent["response"])
                cached["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                return httpx.Response(200, json=cached)
        try:
            request_id = await self.store.submit(
                self.name,
                tenant,
                session,
                payload,
                refresh_seconds=(self.policy.preflight_cache_seconds if self.protected else 60)
                if preflight
                else None,
                logical_run_id=run_id or None,
                operation="preflight" if preflight else "work",
                item_identity="" if preflight else llm_session.get(),
            )
        except LLMRequestDeferredError as exc:
            return self.deferred_response(exc)
        if self.ttl_seconds is not None:
            async with self.store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE jf_llm_requests SET expires_at=LEAST(expires_at,%s) WHERE request_id=%s",
                    (datetime.now(UTC) + timedelta(seconds=self.ttl_seconds), request_id),
                )
        deadline = monotonic() + self.policy.wait_seconds
        while True:
            row = await self.store.get(request_id)
            if self.protected and row["state"] in {"uncertain", "cancelled", "failed"}:
                return self.deferred_response(
                    LLMRequestDeferredError(
                        request_id,
                        datetime.now(UTC) + timedelta(minutes=5),
                        reason=row["error"] or "operator_review",
                        requires_operator=True,
                    )
                )
            if row["state"] in {"succeeded", "failed"} and row["response_status"]:
                return httpx.Response(
                    row["response_status"],
                    json=row["response"],
                    headers=row["response_headers"] or {},
                )
            if row["state"] in {"uncertain", "cancelled", "failed"}:
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "llm_needs_operator",
                            "message": "LLM request requires operator review",
                        }
                    },
                )
            if self.protected or monotonic() >= deadline:
                retry_at = max(row["next_run_at"], datetime.now(UTC) + timedelta(seconds=30))
                if self.protected:
                    return self.deferred_response(
                        LLMRequestDeferredError(
                            request_id,
                            retry_at,
                            reason=row["error"] or "llm_pending",
                            requires_operator=row["error"]
                            in {"auth", "verification_required", "operator"},
                        )
                    )
                return httpx.Response(
                    425,
                    json={
                        "error": {
                            "code": "llm_pending",
                            "request_id": request_id,
                            "retry_at": retry_at.isoformat(),
                            "message": "Durable LLM request pending",
                        }
                    },
                )
            await asyncio.sleep(0.25)

    async def aclose(self) -> None:
        await self.store.close()
