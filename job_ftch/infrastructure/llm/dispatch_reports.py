"""One useful, idempotent aggregate report per completed CLIProxy run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from job_ftch.application.llm_dispatch import LLMRequestDeferredError, run_session_identifier
from job_ftch.infrastructure.llm.dispatch_store import route_revision

if TYPE_CHECKING:
    from job_ftch.infrastructure.llm.dispatch_store import DispatchStore


async def enqueue_run_reports(store: DispatchStore) -> None:
    async with store.pool.connection() as conn:
        runs = await (
            await conn.execute(
                "SELECT * FROM jf_llm_runs WHERE state='completed' AND report_request_id IS NULL ORDER BY updated_at LIMIT 20"
            )
        ).fetchall()
    for run in runs:
        routes = json.loads(run["routes"])
        binding = next(
            (
                b
                for b in store.settings.llm_bindings.values()
                if b.provider in routes and b.capability in {"text", "structured"}
            ),
            None,
        )
        request_id = "skipped:disabled"
        if binding is not None:
            profile = store.settings.llm_provider_profiles[binding.provider]
            policy = store.settings.llm_dispatch_policies[profile.quota_scope or binding.provider]
            enabled = profile.cliproxy_dispatch and policy.run_summary_enabled
            valid = routes[binding.provider] == route_revision(
                store.settings, binding.provider
            ) and run["expires_at"] > datetime.now(UTC)
            async with store.pool.connection() as conn:
                totals = await (
                    await conn.execute(
                        "SELECT count(*) AS requests,sum(attempts) AS attempts,coalesce(sum(actual_tokens),0) AS tokens "
                        "FROM jf_llm_requests WHERE logical_run_id=%s AND operation='work' AND state='succeeded'",
                        (run["run_id"],),
                    )
                ).fetchone()
            if enabled and valid and totals and totals["requests"]:
                summary = {"last_attempt": run["summary"], "inference": dict(totals)}
                payload = {
                    "model": binding.model,
                    "max_tokens": 512,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Analyze only the provided aggregate statistics. last_attempt contains source counters for the latest attempt only; inference covers the whole logical run. Summarize delays and problems needing operator attention in Russian. Do not extrapolate counters, invent causes or request further work.",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(summary, sort_keys=True, default=str),
                        },
                    ],
                }
                try:
                    request_id = await store.submit(
                        binding.provider,
                        run["tenant_id"],
                        run_session_identifier(run["tenant_id"], run["run_id"]),
                        payload,
                        logical_run_id=run["run_id"],
                        operation="run_summary",
                    )
                except LLMRequestDeferredError:
                    continue
                async with store.pool.connection() as conn:
                    await conn.execute(
                        "UPDATE jf_llm_requests SET expires_at=LEAST(expires_at,%s) WHERE request_id=%s",
                        (run["expires_at"], request_id),
                    )
        async with store.pool.connection() as conn:
            await conn.execute(
                "UPDATE jf_llm_runs SET report_request_id=%s WHERE run_id=%s AND report_request_id IS NULL",
                (request_id, run["run_id"]),
            )
