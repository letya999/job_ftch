"""Queue non-streaming compatible HTTP calls without changing Instructor schemas."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any

import httpx
from structlog.contextvars import get_contextvars

from job_ftch.application.llm_dispatch import session_identifier
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

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
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
        preflight = request.headers.get("x-job-ftch-operation") == "preflight"
        if preflight:
            session = "preflight"
        request_id = await self.store.submit(
            self.name, tenant, session, payload, refresh_seconds=60 if preflight else None
        )
        if self.ttl_seconds is not None:
            async with self.store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE jf_llm_requests SET expires_at=LEAST(expires_at,%s) WHERE request_id=%s",
                    (datetime.now(UTC) + timedelta(seconds=self.ttl_seconds), request_id),
                )
        deadline = monotonic() + self.policy.wait_seconds
        while True:
            row = await self.store.get(request_id)
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
            if monotonic() >= deadline:
                retry_at = max(row["next_run_at"], datetime.now(UTC) + timedelta(seconds=30))
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
