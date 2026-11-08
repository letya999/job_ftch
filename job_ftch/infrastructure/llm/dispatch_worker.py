"""Optional Procrastinate worker and secret-free queue administration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import ceil
from pathlib import Path
from tempfile import gettempdir
from typing import Any

import httpx
import procrastinate
from psycopg.types.json import Jsonb

from job_ftch.config import Settings, get_settings
from job_ftch.infrastructure.llm.dispatch_store import DispatchStore, profile_revision


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


def retry_time(value: str | None, *, now: datetime, default_seconds: float) -> datetime:
    try:
        seconds = float(value or "")
        return now + timedelta(seconds=max(seconds, 0))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value or "")
            return max(now, parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
        except (ValueError, TypeError):
            return now + timedelta(seconds=default_seconds)


class LLMWorker:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.store = DispatchStore(settings)
        self.transport = transport
        self.store.app.task(name="job_ftch.llm.execute", queue="llm", retry=DispatchRetry())(
            self.execute_job
        )

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
    ) -> None:
        payload = None
        headers: dict[str, str] = {}
        status = None
        if response is not None:
            status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": {"message": "Upstream returned a non-JSON response"}}
                status = 502
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
            await conn.execute(
                "UPDATE jf_llm_requests SET state=%s,response=%s,response_status=%s,"
                "response_headers=%s,error=%s,updated_at=now() WHERE request_id=%s",
                (state, Jsonb(payload), status, Jsonb(headers), error, request_id),
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
        if row["revision"] != profile_revision(self.settings, row["profile"]):
            await self._finish(request_id, "failed", error="profile_revision_changed")
            return
        if row["expires_at"] <= now or row["attempts"] >= policy.max_attempts:
            await self._finish(request_id, "failed", error="request_expired_or_exhausted")
            return
        payload = row["payload"]
        if profile.egress_ready_url:
            try:
                async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                    ready = (await client.get(profile.egress_ready_url)).status_code == 200
            except httpx.HTTPError:
                ready = False
            if not ready:
                await self._defer(row, min(now + timedelta(seconds=30), row["expires_at"]))
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
            now = datetime.now(UTC)
            # Procrastinate's account lock serializes network calls; this transaction
            # reserves shared time and budgets before any upstream request.
            if quota["window_start"] + timedelta(hours=1) <= now:
                quota.update(window_start=now, calls=0, tokens=0)
            wait_until = max(now, quota["not_before"], row["next_run_at"])
            if (
                quota["calls"] >= policy.max_calls_per_hour
                or quota["tokens"] + estimate > policy.max_tokens_per_hour
            ):
                wait_until = max(wait_until, quota["window_start"] + timedelta(hours=1))
            if quota["paused_reason"]:
                wait_until = max(wait_until, now + timedelta(seconds=60))
            if wait_until > now:
                await conn.execute(
                    "UPDATE jf_llm_requests SET next_run_at=%s,updated_at=now() WHERE request_id=%s",
                    (wait_until, request_id),
                )
            else:
                await conn.execute(
                    "UPDATE jf_llm_scopes SET window_start=%s,calls=%s,tokens=%s,not_before=%s "
                    "WHERE scope=%s",
                    (
                        quota["window_start"],
                        quota["calls"] + 1,
                        quota["tokens"] + estimate,
                        now + timedelta(seconds=policy.min_interval_seconds),
                        scope,
                    ),
                )
                await conn.execute(
                    "UPDATE jf_llm_requests SET state='running',attempts=attempts+1,"
                    "reserved_tokens=%s,updated_at=now() WHERE request_id=%s",
                    (estimate, request_id),
                )
        if wait_until > now:
            raise DispatchWait(min(wait_until, row["expires_at"]))
        base_url = profile.base_url or self.settings.openai_base_url or "https://api.openai.com/v1"
        headers = {"Authorization": f"Bearer {credential}"}
        if profile.session_affinity:
            headers["X-Session-ID"] = row["session_id"]
        try:
            async with httpx.AsyncClient(
                timeout=profile.timeout_seconds,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            await self._defer(row, now + timedelta(seconds=policy.retry_delay_seconds))
            return
        except httpx.HTTPError:
            await self._finish(request_id, "uncertain", error="upstream_outcome_unknown")
            return
        if len(response.content) > 4_000_000:
            await self._finish(request_id, "failed", error="response_too_large")
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

    async def _defer(self, row: dict[str, Any], due: datetime) -> None:
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "UPDATE jf_llm_requests SET state='pending',next_run_at=%s,updated_at=now() "
                "WHERE request_id=%s",
                (due, row["request_id"]),
            )
        raise DispatchWait(due)

    async def maintain(self) -> None:
        """Reconcile dead jobs conservatively; never replay an unknown HTTP outcome."""
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
                "DELETE FROM jf_llm_requests WHERE state IN ('succeeded','failed','cancelled') "
                "AND updated_at < now() - (%s * interval '1 second')",
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
            await self.store.close()


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
        else:
            await store.open()
            async with store.pool.connection() as conn:
                if args.command in {"pause", "resume"}:
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
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["init", "worker", "status", "pause", "resume", "cancel"]
    )
    parser.add_argument("target", nargs="?")
    args = parser.parse_args()
    if args.command in {"pause", "resume", "cancel"} and not args.target:
        parser.error("target scope/request ID is required")
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(admin(args))
    else:
        asyncio.run(admin(args))


if __name__ == "__main__":
    main()
