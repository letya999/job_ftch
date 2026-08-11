"""Resume-driven search session orchestration (PR 6).

Coordinates existing TenantRunner primitives:
- candidate profile ingest/reuse
- source listing
- browser route planner diagnostics (PR 5)
- tenant pipeline runs with source_ids filter
- latest/search job ranking against the session profile

Does not implement separate relevance logic, browser execution, CAPTCHA solving,
login automation, or credential harvesting.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.browser_capability_inventory import (
    explanation_to_public_dict,
)
from job_ftch.domain.search_session import (
    DEFAULT_SESSION_PRIVACY_NOTES,
    SearchResultRef,
    SearchSession,
    SearchSessionApproval,
    SearchSessionBudgets,
    SearchSessionExplanation,
    SearchSessionStatus,
    SourceRoutePlanEntry,
    SourceSessionStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.domain import JobRecord, ManagedCandidateProfile
    from job_ftch.domain.browser_capability_inventory import (
        BrowserCapabilityEntry,
        RoutePlanExplanation,
    )

logger = structlog.get_logger(__name__)

_SENSITIVE_GROUPS = frozenset(
    {
        "browser",
        "persistent_session",
        "proxy_backed",
        "manual_challenge",
    }
)

_TERMINAL_STATUSES: frozenset[SearchSessionStatus] = frozenset(
    {"completed", "cancelled", "failed"}
)

# Cooperative cancel flags for in-process runs. Store-backed cancel_requested is
# the durable signal; this set makes mid-run checks cheap without polling store.
_INFLIGHT_CANCEL: set[str] = set()


class SearchSessionError(ValueError):
    """Domain/application error for search session lifecycle violations."""


def _now() -> datetime:
    return datetime.now(UTC)


def _new_session_id() -> str:
    return uuid.uuid4().hex


def session_to_public_dict(session: SearchSession) -> dict[str, Any]:
    """Serialize session state with sensitive-key scrubbing."""
    payload = session.model_dump(mode="json")
    return _redact_payload(payload)


def explanation_to_dict(explanation: SearchSessionExplanation) -> dict[str, Any]:
    return _redact_payload(explanation.model_dump(mode="json"))


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(
                marker in lowered
                for marker in (
                    "cookie",
                    "token",
                    "password",
                    "secret",
                    "proxy_url",
                    "authorization",
                    "profile_path",
                    "executable",
                    "api_key",
                    "resume_text",
                    "raw_text",
                )
            ):
                continue
            redacted[str(key)] = _redact_payload(nested)
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_payload(item) for item in value]
    return value


async def _load_session(runner: TenantRunner, session_id: str) -> SearchSession:
    tenant_ids = runner.tenant_ids()
    # Prefer looking up by scanning known tenants; sessions are tenant-scoped.
    for tenant_id in tenant_ids:
        runtime = runner.get_runtime(tenant_id)
        session = await runtime.store.get_search_session(session_id)
        if session is not None:
            return session
    msg = f"search session not found: {session_id}"
    raise SearchSessionError(msg)


async def _save_session(runner: TenantRunner, session: SearchSession) -> SearchSession:
    runtime = runner.get_runtime(session.tenant_id)
    session.updated_at = _now()
    await runtime.store.save_search_session(session)
    return session


def _requires_approval_for_route(
    explanation: RoutePlanExplanation,
    *,
    inventory_by_id: dict[str, BrowserCapabilityEntry],
) -> bool:
    selected = explanation.selected_capability_id
    if selected and selected in inventory_by_id:
        entry = inventory_by_id[selected]
        if entry.requires_approval:
            return True
        if entry.group in _SENSITIVE_GROUPS:
            return True
    return explanation.selected_group in _SENSITIVE_GROUPS


def _risk_for_route(
    explanation: RoutePlanExplanation,
    *,
    inventory_by_id: dict[str, BrowserCapabilityEntry],
) -> str | None:
    selected = explanation.selected_capability_id
    if selected and selected in inventory_by_id:
        return inventory_by_id[selected].risk
    return None


def _resolve_source_scope(
    sources: Sequence[dict[str, Any]],
    source_scope: Sequence[str] | None,
    *,
    max_sources: int | None,
) -> list[dict[str, Any]]:
    if source_scope:
        wanted = {str(item) for item in source_scope}
        selected = [item for item in sources if str(item.get("source_id") or "") in wanted]
        missing = wanted - {str(item.get("source_id") or "") for item in selected}
        if missing:
            # Keep explicit missing ids as synthetic disabled rows for planning.
            for source_id in sorted(missing):
                selected.append(
                    {
                        "source_id": source_id,
                        "source_kind": None,
                        "source_name": source_id,
                        "enabled": False,
                        "status": "skipped",
                        "degraded": False,
                    }
                )
    else:
        selected = [item for item in sources if bool(item.get("enabled", True))]
    if max_sources is not None:
        selected = selected[: max(0, max_sources)]
    return selected


async def create_search_session(
    runner: TenantRunner,
    *,
    tenant_id: str,
    user_id: str | None = None,
    profile_id: str | None = None,
    source_scope: Sequence[str] | None = None,
    budgets: SearchSessionBudgets | None = None,
) -> SearchSession:
    """Create a session bound to a tenant and optional candidate profile."""
    runtime = runner.get_runtime(tenant_id)
    resolved_profile_id = profile_id
    if user_id and resolved_profile_id is None:
        resolved_profile_id = await runtime.store.get_active_candidate_profile_id(user_id)
    if user_id and resolved_profile_id:
        record = await runtime.store.get_candidate_profile(user_id, resolved_profile_id)
        if record is None:
            msg = f"candidate profile not found: user_id={user_id} profile_id={resolved_profile_id}"
            raise SearchSessionError(msg)

    now = _now()
    session = SearchSession(
        session_id=_new_session_id(),
        tenant_id=tenant_id,
        user_id=user_id,
        profile_id=resolved_profile_id,
        status="created",
        source_scope=tuple(str(item) for item in (source_scope or ())),
        budgets=budgets or SearchSessionBudgets(),
        created_at=now,
        updated_at=now,
        notes=("created; call plan_source_routes next",),
        privacy_notes=DEFAULT_SESSION_PRIVACY_NOTES,
        provenance={
            "layer": "search_session",
            "profile_required_for_ranking": bool(user_id or resolved_profile_id),
        },
    )
    await runtime.store.save_search_session(session)
    logger.info(
        "search_session_created",
        session_id=session.session_id,
        tenant_id=tenant_id,
        has_profile=bool(resolved_profile_id),
        source_scope_count=len(session.source_scope),
    )
    return session


async def plan_source_routes(
    runner: TenantRunner,
    session_id: str,
) -> SearchSession:
    """Build per-source route plans using browser capability inventory diagnostics."""
    session = await _load_session(runner, session_id)
    if session.status in _TERMINAL_STATUSES:
        msg = f"cannot plan routes for terminal session status={session.status}"
        raise SearchSessionError(msg)
    if session.cancel_requested or session_id in _INFLIGHT_CANCEL:
        return await cancel_search_session(runner, session_id)

    sources = await runner.list_sources(session.tenant_id)
    scoped = _resolve_source_scope(
        sources,
        session.source_scope or None,
        max_sources=session.budgets.max_sources,
    )
    inventory = runner.list_browser_capabilities(session.tenant_id)
    inventory_by_id = {item.id: item for item in inventory.capabilities}

    route_plan: list[SourceRoutePlanEntry] = []
    for source in scoped:
        source_id = str(source.get("source_id") or "")
        enabled = bool(source.get("enabled", True))
        if not enabled:
            route_plan.append(
                SourceRoutePlanEntry(
                    source_id=source_id,
                    source_kind=str(source.get("source_kind") or "") or None,
                    source_name=str(source.get("source_name") or "") or None,
                    enabled=False,
                    status="skipped",
                    requires_approval=False,
                    reason="source disabled or not in enabled catalog",
                )
            )
            continue

        explanation = await runner.explain_browser_route(
            session.tenant_id,
            source_id,
        )
        if explanation.error:
            route_plan.append(
                SourceRoutePlanEntry(
                    source_id=source_id,
                    source_kind=explanation.source_kind or str(source.get("source_kind") or "") or None,
                    source_name=str(source.get("source_name") or "") or None,
                    enabled=True,
                    status="failed",
                    reason=explanation.error,
                    error=explanation.error,
                    diagnostics=explanation.diagnostics,
                    route_notes=explanation.notes,
                )
            )
            continue

        requires_approval = _requires_approval_for_route(
            explanation,
            inventory_by_id=inventory_by_id,
        )
        degraded = bool(source.get("degraded"))
        status: SourceSessionStatus = "degraded" if degraded else "pending"
        if explanation.selected_group == "manual_challenge":
            status = "needs_manual"
            requires_approval = True
        elif explanation.selected_capability_id is None:
            status = "skipped"
            requires_approval = False

        route_plan.append(
            SourceRoutePlanEntry(
                source_id=source_id,
                source_kind=explanation.source_kind or str(source.get("source_kind") or "") or None,
                source_name=str(source.get("source_name") or "") or None,
                enabled=True,
                status=status,
                selected_capability_id=explanation.selected_capability_id,
                selected_group=explanation.selected_group,
                requires_approval=requires_approval,
                risk=cast(
                    "Any",
                    _risk_for_route(explanation, inventory_by_id=inventory_by_id),
                ),
                reason=(
                    "sensitive route requires explicit approval"
                    if requires_approval
                    else "route selected"
                    if explanation.selected_capability_id
                    else "no available route"
                ),
                diagnostics=explanation.diagnostics,
                route_notes=explanation.notes,
            )
        )

    needs_approval = any(item.requires_approval and not item.approved for item in route_plan)
    session.route_plan = tuple(route_plan)
    session.selected_source_ids = tuple(
        item.source_id for item in route_plan if item.enabled and item.status != "skipped"
    )
    session.planned_at = _now()
    session.status = "awaiting_approval" if needs_approval else "planned"
    session.notes = (
        *(session.notes or ()),
        f"planned {len(route_plan)} source route(s)",
        "awaiting approval for sensitive routes" if needs_approval else "no sensitive approval required",
    )
    session.provenance = {
        **(session.provenance or {}),
        "route_plan_generated_at": session.planned_at.isoformat(),
        "capability_inventory_status": inventory.status,
    }
    return await _save_session(runner, session)


async def approve_search_session(
    runner: TenantRunner,
    session_id: str,
    *,
    approved_source_ids: Sequence[str] | None = None,
    approved_capability_ids: Sequence[str] | None = None,
    approve_all_sensitive: bool = False,
    note: str | None = None,
) -> SearchSession:
    """Record approvals for sensitive routes/budgets before run."""
    session = await _load_session(runner, session_id)
    if session.status in _TERMINAL_STATUSES:
        msg = f"cannot approve terminal session status={session.status}"
        raise SearchSessionError(msg)
    if session.status == "created":
        msg = "plan_source_routes must run before approve_search_session"
        raise SearchSessionError(msg)
    if session.cancel_requested or session_id in _INFLIGHT_CANCEL:
        return await cancel_search_session(runner, session_id)

    approved_sources = {str(item) for item in (approved_source_ids or ())}
    approved_caps = {str(item) for item in (approved_capability_ids or ())}
    updated_plan: list[SourceRoutePlanEntry] = []
    for entry in session.route_plan:
        if not entry.requires_approval:
            updated_plan.append(entry.model_copy(update={"approved": True}))
            continue
        approved = bool(
            approve_all_sensitive
            or entry.source_id in approved_sources
            or (entry.selected_capability_id and entry.selected_capability_id in approved_caps)
        )
        if approved:
            updated_plan.append(entry.model_copy(update={"approved": True}))
        else:
            # Unapproved sensitive routes are skipped at run time.
            updated_plan.append(
                entry.model_copy(
                    update={
                        "approved": False,
                        "status": "skipped",
                        "reason": entry.reason or "sensitive route not approved",
                    }
                )
            )

    approval = SearchSessionApproval(
        approved_source_ids=tuple(sorted(approved_sources)),
        approved_capability_ids=tuple(sorted(approved_caps)),
        approve_all_sensitive=approve_all_sensitive,
        note=note,
        approved_at=_now(),
    )
    session.route_plan = tuple(updated_plan)
    session.approval = approval
    session.status = "approved"
    session.selected_source_ids = tuple(
        item.source_id
        for item in updated_plan
        if item.enabled
        and item.status not in {"skipped", "failed"}
        and (not item.requires_approval or item.approved)
    )
    session.notes = (
        *(session.notes or ()),
        f"approval recorded; runnable sources={len(session.selected_source_ids)}",
    )
    return await _save_session(runner, session)


def _result_ref_from_job(job: JobRecord) -> SearchResultRef:
    source_identity = getattr(job, "source_identity", None)
    source_id = None
    if source_identity is not None:
        source_id = getattr(source_identity, "source_id", None) or getattr(
            source_identity, "canonical_id", None
        )
    routing = job.routing_decision
    return SearchResultRef(
        job_id=job.job_id,
        group_id=job.group_id,
        source_id=str(source_id) if source_id else None,
        source_name=job.source_name,
        title=job.title,
        best_score=job.best_score,
        routing_decision=str(routing.value) if routing is not None else None,
    )


def _lookup_source_stats(summary: Any, entry: SourceRoutePlanEntry) -> Any | None:
    """Resolve per-source stats from RunSummary.by_source_id / by_source_kind."""
    by_source_id = getattr(summary, "by_source_id", None)
    if isinstance(by_source_id, dict):
        if entry.source_id in by_source_id:
            return by_source_id[entry.source_id]
        if entry.source_name:
            for key, stats in by_source_id.items():
                if str(key).endswith(f":{entry.source_name}") or str(key) == entry.source_name:
                    return stats
    by_kind = getattr(summary, "by_source_kind", None)
    if isinstance(by_kind, dict) and entry.source_kind and entry.source_kind in by_kind:
        return by_kind[entry.source_kind]
    # Test/fake summaries may attach a plain mapping under source_stats_map.
    extra = getattr(summary, "source_stats_map", None)
    if isinstance(extra, dict):
        return extra.get(entry.source_id) or extra.get(entry.source_name or "")
    return None


def _apply_run_outcomes(
    session: SearchSession,
    *,
    summary: Any,
    jobs: Sequence[JobRecord],
) -> SearchSession:
    drop_reasons = dict(getattr(summary, "drop_reasons", {}) or {})
    updated_plan: list[SourceRoutePlanEntry] = []
    degraded: list[str] = []

    for entry in session.route_plan:
        if entry.status == "skipped" or not entry.enabled:
            updated_plan.append(entry)
            continue
        if entry.requires_approval and not entry.approved:
            updated_plan.append(
                entry.model_copy(
                    update={
                        "status": "skipped",
                        "reason": "sensitive route not approved",
                    }
                )
            )
            continue

        stats = _lookup_source_stats(summary, entry)

        new_status: SourceSessionStatus = "checked"
        reason = entry.reason
        if stats is not None:
            failed = int(getattr(stats, "failed", 0) or 0)
            emitted = int(getattr(stats, "emitted", 0) or 0)
            fetched = int(getattr(stats, "fetched", 0) or 0)
            partial = bool(getattr(stats, "source_partial", False))
            if failed > 0 and emitted == 0:
                new_status = "failed"
                reason = "source run failed without emissions"
            elif partial or (failed > 0 and emitted > 0):
                new_status = "degraded"
                reason = "source run partial or mixed failure"
            elif fetched == 0 and emitted == 0:
                new_status = "no_results"
                reason = "source returned no items"
            else:
                new_status = "checked"
                reason = "source checked"
        elif not jobs:
            new_status = "no_results"
            reason = "no ranked results after run"

        if new_status in {"degraded", "failed"}:
            degraded.append(entry.source_id)
        updated_plan.append(entry.model_copy(update={"status": new_status, "reason": reason}))

    result_refs = tuple(_result_ref_from_job(job) for job in jobs)
    session.route_plan = tuple(updated_plan)
    session.result_refs = result_refs
    session.rejected_summary = {str(k): int(v) for k, v in drop_reasons.items()}
    session.degraded_source_ids = tuple(sorted(set(degraded)))
    run_id = getattr(summary, "source_run_id", None)
    if run_id:
        session.run_ids = (*session.run_ids, str(run_id))
    session.provenance = {
        **(session.provenance or {}),
        "last_run_summary": {
            "source_run_id": getattr(summary, "source_run_id", None),
            "fetched": getattr(summary, "fetched", 0),
            "emitted": getattr(summary, "emitted", 0),
            "failed": getattr(summary, "failed", 0),
            "rejected": getattr(summary, "rejected", 0),
            "skipped_already_active": getattr(summary, "skipped_already_active", False),
        },
    }
    return session


async def run_search_session(
    runner: TenantRunner,
    session_id: str,
    *,
    skip_pipeline: bool = False,
) -> SearchSession:
    """Execute approved session routes via TenantRunner.run_tenant + ranking.

    ``skip_pipeline=True`` only ranks existing jobs (useful for tests/diagnostics).
    Cancellation is cooperative: cancel_requested is checked before run; mid-run
    kill of browser tasks relies on existing runner teardown on completion/close.
    """
    session = await _load_session(runner, session_id)
    if session.status in _TERMINAL_STATUSES:
        msg = f"cannot run terminal session status={session.status}"
        raise SearchSessionError(msg)
    if session.status not in {"planned", "approved", "awaiting_approval"}:
        if session.status == "created":
            msg = "plan_source_routes must run before run_search_session"
            raise SearchSessionError(msg)
        if session.status == "running":
            msg = "search session is already running"
            raise SearchSessionError(msg)

    # Auto-approve non-sensitive plans that never needed approval.
    if session.status == "planned":
        session = await approve_search_session(
            runner,
            session_id,
            approve_all_sensitive=False,
            note="auto-approved: no pending sensitive routes",
        )
    elif session.status == "awaiting_approval":
        pending = [
            item.source_id
            for item in session.route_plan
            if item.requires_approval and not item.approved and item.enabled
        ]
        if pending:
            msg = (
                "sensitive routes require approve_search_session before run: "
                + ", ".join(pending[:10])
            )
            raise SearchSessionError(msg)
        session = await approve_search_session(
            runner,
            session_id,
            approve_all_sensitive=False,
            note="auto-approved: sensitive routes already cleared or skipped",
        )

    if session.cancel_requested or session_id in _INFLIGHT_CANCEL:
        return await cancel_search_session(runner, session_id)

    runnable_ids = list(session.selected_source_ids)
    if not runnable_ids:
        session.status = "completed"
        session.finished_at = _now()
        session.notes = (*(session.notes or ()), "no runnable sources after planning/approval")
        session.result_refs = ()
        return await _save_session(runner, session)

    session.status = "running"
    session.started_at = _now()
    session = await _save_session(runner, session)

    try:
        if session.cancel_requested or session_id in _INFLIGHT_CANCEL:
            session.status = "cancelled"
            session.finished_at = _now()
            session.notes = (*(session.notes or ()), "cancelled before pipeline start")
            return await _save_session(runner, session)

        summary = None
        if not skip_pipeline:
            summary = await runner.run_tenant(
                session.tenant_id,
                max_items=session.budgets.max_items,
                user_id=session.user_id,
                source_ids=runnable_ids,
            )
            if session_id in _INFLIGHT_CANCEL or session.cancel_requested:
                # Cooperative: pipeline may have finished; mark cancelled and keep partial results.
                session.cancel_requested = True
                reloaded = await _load_session(runner, session_id)
                session.cancel_requested = True
                session.notes = (
                    *(reloaded.notes or ()),
                    "cancel requested during run; pipeline stop is cooperative",
                )

        jobs = await runner.latest_jobs(
            session.tenant_id,
            limit=session.budgets.result_limit,
            user_id=session.user_id,
            profile_id=session.profile_id,
        )
        # Reuse existing ranking; optional soft filter when source_name matches session.
        selected = set(runnable_ids)
        names = {
            item.source_name
            for item in session.route_plan
            if item.source_id in selected and item.source_name
        }
        if names:
            matched = [job for job in jobs if job.source_name in names]
            ranked = matched or list(jobs)
        else:
            ranked = list(jobs)
        ranked = ranked[: session.budgets.result_limit]

        if summary is None:
            from job_ftch.application.pipeline import RunSummary

            summary = RunSummary()
            summary.tenant_id = session.tenant_id
            summary.source_run_id = f"search-session-rank-only:{session.session_id}"
            summary.finished_at = _now()

        session = _apply_run_outcomes(session, summary=summary, jobs=ranked)
        if session.cancel_requested or session_id in _INFLIGHT_CANCEL:
            session.status = "cancelled"
            session.finished_at = _now()
            _INFLIGHT_CANCEL.discard(session_id)
            session.notes = (*(session.notes or ()), "session cancelled after cooperative stop")
            return await _save_session(runner, session)

        session.status = "completed"
        session.finished_at = _now()
        session.notes = (
            *(session.notes or ()),
            f"run completed with {len(session.result_refs)} result(s)",
        )
        return await _save_session(runner, session)
    except SearchSessionError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as failed session state
        logger.exception(
            "search_session_run_failed",
            session_id=session_id,
            tenant_id=session.tenant_id,
        )
        session.status = "failed"
        session.error = str(exc)[:500]
        session.finished_at = _now()
        session.notes = (*(session.notes or ()), "run failed")
        _INFLIGHT_CANCEL.discard(session_id)
        return await _save_session(runner, session)


async def get_search_session_status(
    runner: TenantRunner,
    session_id: str,
) -> SearchSession:
    """Return current session state (status, routes, budgets, run ids)."""
    return await _load_session(runner, session_id)


async def list_search_results(
    runner: TenantRunner,
    session_id: str,
    *,
    limit: int | None = None,
) -> list[SearchResultRef]:
    """List ranked result refs captured for the session."""
    session = await _load_session(runner, session_id)
    refs = list(session.result_refs)
    if limit is not None:
        refs = refs[: max(0, limit)]
    return refs


async def explain_rejected_or_degraded(
    runner: TenantRunner,
    session_id: str,
    *,
    source_id: str | None = None,
    job_id: str | None = None,
) -> SearchSessionExplanation:
    """Explain why a source was degraded/skipped or a job was not selected."""
    if not source_id and not job_id:
        msg = "source_id or job_id is required"
        raise SearchSessionError(msg)

    session = await _load_session(runner, session_id)
    if source_id:
        entry = next((item for item in session.route_plan if item.source_id == source_id), None)
        if entry is None:
            return SearchSessionExplanation(
                session_id=session_id,
                target_type="source",
                target_id=source_id,
                status="unknown",
                reasons=("source not part of this session route plan",),
                notes=("re-run plan_source_routes if source was added later",),
            )
        reasons = [r for r in (entry.reason, entry.error) if r]
        if entry.status == "skipped" and entry.requires_approval and not entry.approved:
            reasons.append("sensitive route was not approved")
        if entry.source_id in session.degraded_source_ids:
            reasons.append("source marked degraded after run")
        # Refresh route diagnostics for currency without executing browsers.
        explanation = await runner.explain_browser_route(session.tenant_id, source_id)
        public_route = explanation_to_public_dict(explanation)
        return SearchSessionExplanation(
            session_id=session_id,
            target_type="source",
            target_id=source_id,
            status=entry.status,
            reasons=tuple(reasons) or ("no explicit rejection reason recorded",),
            diagnostics=entry.diagnostics or explanation.diagnostics,
            notes=entry.route_notes + explanation.notes,
            evidence={
                "selected_capability_id": entry.selected_capability_id,
                "selected_group": entry.selected_group,
                "requires_approval": entry.requires_approval,
                "approved": entry.approved,
                "route": public_route,
                "rejected_summary": dict(session.rejected_summary),
            },
        )

    assert job_id is not None
    ref = next((item for item in session.result_refs if item.job_id == job_id), None)
    lineage = await runner.get_job_lineage(job_id, tenant_id=session.tenant_id)
    reasons: list[str] = []
    notes: list[str] = []
    evidence: dict[str, object] = {"in_session_results": ref is not None}
    if ref is None:
        reasons.append("job is not in session result set")
        notes.append("job may have been filtered by existing relevance/evidence pipeline")
    else:
        if ref.routing_decision:
            reasons.append(f"routing_decision={ref.routing_decision}")
        if ref.best_score is not None:
            reasons.append(f"best_score={ref.best_score}")
        evidence["result_ref"] = ref.model_dump(mode="json")
    if lineage is not None:
        evidence["lineage"] = lineage.model_dump(mode="json")
        for key in ("source_run_id", "source_name", "raw_item_id", "group_id"):
            value = getattr(lineage, key, None)
            if value:
                reasons.append(f"{key}={value}")
    if session.rejected_summary:
        evidence["session_rejected_summary"] = dict(session.rejected_summary)
        notes.append("session rejected_summary aggregates pipeline drop reasons for the run")
    return SearchSessionExplanation(
        session_id=session_id,
        target_type="job",
        target_id=job_id,
        status="selected" if ref is not None else "not_selected",
        reasons=tuple(reasons) or ("no additional decision evidence available",),
        notes=tuple(notes),
        evidence=evidence,
    )


async def cancel_search_session(
    runner: TenantRunner,
    session_id: str,
) -> SearchSession:
    """Request cancellation; cooperative if a run is in flight."""
    session = await _load_session(runner, session_id)
    if session.status in {"completed", "cancelled", "failed"}:
        return session

    _INFLIGHT_CANCEL.add(session_id)
    session.cancel_requested = True
    if session.status == "running":
        session.notes = (
            *(session.notes or ()),
            "cancel requested; in-flight pipeline stop is cooperative",
        )
        # Leave status=running until run_search_session observes the flag, but
        # also flip to cancelled if no runner is actively managing this process.
        session.status = "cancelled"
        session.finished_at = _now()
    else:
        session.status = "cancelled"
        session.finished_at = _now()
        session.notes = (*(session.notes or ()), "session cancelled before/without run")
        # Mark pending routes as skipped.
        session.route_plan = tuple(
            item.model_copy(
                update={
                    "status": "skipped" if item.status == "pending" else item.status,
                    "reason": item.reason or "session cancelled",
                }
            )
            for item in session.route_plan
        )
    session = await _save_session(runner, session)
    _INFLIGHT_CANCEL.discard(session_id)
    logger.info(
        "search_session_cancelled",
        session_id=session_id,
        tenant_id=session.tenant_id,
        status=session.status,
    )
    return session


async def ingest_resume(
    runner: TenantRunner,
    *,
    tenant_id: str,
    user_id: str,
    resume_text: str,
    profile_id: str | None = None,
    activate: bool = True,
) -> ManagedCandidateProfile:
    """Ingest resume text into a managed candidate profile (no session body retention)."""
    from job_ftch.application.resume_extraction import build_profile_from_resume_text_async

    text = (resume_text or "").strip()
    if not text:
        msg = "resume_text must not be empty"
        raise SearchSessionError(msg)
    # Bound size; full resume is not stored on the session object.
    text = text[:20000]
    runtime = runner.get_runtime(tenant_id)
    llm = getattr(runtime, "llm_provider", None)
    record = await build_profile_from_resume_text_async(
        text,
        user_id=user_id,
        profile_id=profile_id,
        llm_provider=llm,
    )
    if activate:
        await runner.save_and_activate_candidate_profile(tenant_id, record)
    else:
        await runner.save_candidate_profile(tenant_id, record)
    return record
