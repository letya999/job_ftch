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

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.browser_capability_inventory import (
    explanation_to_public_dict,
)
from job_ftch.domain.search_session import (
    DEFAULT_SESSION_PRIVACY_NOTES,
    ManualChallengeInfo,
    ManualChallengeReasonCode,
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
    from job_ftch.domain.lineage import JobLineage

logger = structlog.get_logger(__name__)

_SENSITIVE_GROUPS = frozenset(
    {
        "browser",
        "persistent_session",
        "proxy_backed",
        "manual_challenge",
    }
)

# Key denylist for public/explain payloads (substring match on key names).
_SENSITIVE_KEY_MARKERS = (
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
    "raw_html",
    "cookie_jar",
)

# Value scrubbing aligned with browser_capability_inventory, plus HTML and
# internal-network markers. Intentionally avoids natural-language phrases that
# appear in privacy_notes (e.g. "resume body", "cookies") so documentation is
# not self-redacted. Route ids / source ids / capability labels stay intact.
_SENSITIVE_SNIPPET_MARKERS = (
    "http://",
    "https://",
    "socks5://",
    "socks4://",
    "bearer ",
    "cookie=",
    "set-cookie:",
    "authorization:",
    "api_key",
    "token=",
    "password=",
    ".runtime/",
    "c:\\",
    "/home/",
    "/users/",
    "<html",
    "<!doctype",
    "<script",
    "</body>",
    "</html>",
)

_PRIVATE_NETWORK_RE = re.compile(
    r"(?i)\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"127\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"localhost|"
    r"[a-z0-9][a-z0-9.-]*\.(?:local|internal|lan|corp))"
    r"(?::\d{2,5})?\b"
)

# Routes that require human-in-the-loop; never auto-executed by run_search_session.
_MANUAL_GROUPS = frozenset({"manual_challenge"})

_TERMINAL_STATUSES: frozenset[SearchSessionStatus] = frozenset({"completed", "cancelled", "failed"})

_MANUAL_ACTION_HINTS: dict[ManualChallengeReasonCode, str] = {
    "login_required": (
        "Operator must complete site login in an approved headed session; "
        "the search session will not store credentials or cookies."
    ),
    "challenge_required": (
        "Operator must complete the site challenge (or wait for clearance) "
        "outside automatic bypass; no CAPTCHA solver is claimed here."
    ),
    "captcha_required": (
        "A CAPTCHA or interactive challenge was selected; provide operator approval "
        "and handle manually — automated solve is not guaranteed."
    ),
    "auth_wall": (
        "Source is behind an authentication wall; use an approved manual route "
        "or skip the source. Credentials are never collected by this session."
    ),
    "manual_route_selected": (
        "Planner selected a manual-challenge route. Approve the budget/risk, then "
        "complete the challenge manually; automatic bypass is not performed."
    ),
    "operator_approval_required": (
        "Sensitive challenge route requires explicit operator approval before any "
        "further action; approval is consent, not automated login."
    ),
}

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
    """Serialize session state with sensitive-key and value scrubbing."""
    payload = session.model_dump(mode="json")
    return cast("dict[str, Any]", _redact_payload(payload))


def explanation_to_dict(explanation: SearchSessionExplanation) -> dict[str, Any]:
    """Serialize explain payload with sensitive-key and value scrubbing."""
    return cast("dict[str, Any]", _redact_payload(explanation.model_dump(mode="json")))


def _string_looks_sensitive(text: str) -> bool:
    """Return True when a free-text value should not leave the public surface."""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_SNIPPET_MARKERS):
        return True
    return _PRIVATE_NETWORK_RE.search(text) is not None


def _redact_payload(value: Any) -> Any:
    """Redact public/explain payloads by sensitive keys and sensitive-looking values.

    Key denylist drops secret-bearing fields entirely. String values that look
    like cookies/tokens/proxy URLs/browser paths/HTML/internal endpoints are
    replaced with ``redacted``. Allowed public identifiers (route ids, source
    ids, capability labels, status codes) are left intact.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
                continue
            redacted[str(key)] = _redact_payload(nested)
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        if _string_looks_sensitive(value):
            return "redacted"
        return value
    return value


def _public_lineage_evidence(lineage: JobLineage) -> dict[str, Any]:
    """Trim JobLineage to a small public-safe explain evidence shape.

    Keeps useful source/job provenance (kind, name, stages, timestamps, group
    presence) while omitting tenant/user/private ids, raw item/run ids, full
    provenance trails, sibling job id lists, and raw URL blobs.
    """
    source_kind = getattr(lineage, "source_kind", None)
    kind_text = getattr(source_kind, "value", source_kind)
    fetched_at = getattr(lineage, "fetched_at", None)
    posted_at = getattr(lineage, "posted_at", None)
    stages = getattr(lineage, "pipeline_stages", ()) or ()
    group_id = getattr(lineage, "group_id", None)
    group_job_ids = getattr(lineage, "group_job_ids", ()) or ()
    provenance = getattr(lineage, "provenance", None)
    provenance_present = False
    if provenance is not None:
        for field_name in ("extraction", "normalization", "merge"):
            if getattr(provenance, field_name, ()):
                provenance_present = True
                break
        if not provenance_present and isinstance(provenance, dict):
            provenance_present = any(
                bool(provenance.get(name)) for name in ("extraction", "normalization", "merge")
            )

    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        text = str(value).strip()
        return text or None

    return {
        "source_kind": str(kind_text) if kind_text is not None else None,
        "source_name": getattr(lineage, "source_name", None),
        "pipeline_stages": [str(stage) for stage in stages],
        "group_source_count": int(getattr(lineage, "group_source_count", 1) or 1),
        "has_group": bool(group_id or group_job_ids),
        "has_source_url": getattr(lineage, "source_url", None) is not None,
        "has_canonical_url": getattr(lineage, "canonical_url", None) is not None,
        "fetched_at": _iso(fetched_at),
        "posted_at": _iso(posted_at),
        "provenance_present": provenance_present,
    }


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


def _reason_code_for_manual_route(
    explanation: RoutePlanExplanation,
    *,
    inventory_by_id: dict[str, BrowserCapabilityEntry],
) -> ManualChallengeReasonCode:
    """Map route diagnostics to a stable public reason code (no secrets)."""
    selected = explanation.selected_capability_id
    entry = inventory_by_id.get(selected) if selected else None
    engine = (entry.engine if entry is not None else None) or ""
    blob = " ".join(
        part
        for part in (
            explanation.selected_group or "",
            engine,
            entry.reason if entry is not None else "",
            *(item.reason for item in explanation.diagnostics),
            *explanation.notes,
        )
        if part
    ).lower()
    # Prefer specific challenge/captcha signals over generic "login" wording that
    # may appear in advisory notes ("no automated login").
    if "captcha" in blob or engine == "captcha_solver":
        return "captcha_required"
    if "auth_wall" in blob or "auth wall" in blob:
        return "auth_wall"
    if "challenge" in blob:
        return "challenge_required"
    if "login" in blob:
        return "login_required"
    if explanation.selected_group in _MANUAL_GROUPS:
        return "manual_route_selected"
    return "operator_approval_required"


def _deadline_for_manual_route(
    *,
    budgets: SearchSessionBudgets,
    explanation: RoutePlanExplanation,
    inventory_by_id: dict[str, BrowserCapabilityEntry],
) -> float | None:
    if budgets.deadline_seconds is not None:
        return float(budgets.deadline_seconds)
    selected = explanation.selected_capability_id
    if selected and selected in inventory_by_id:
        timeout = inventory_by_id[selected].hard_timeout_seconds
        if timeout is not None:
            return float(timeout)
    return None


def _build_manual_challenge(
    *,
    source_id: str,
    source_label: str | None,
    explanation: RoutePlanExplanation,
    inventory_by_id: dict[str, BrowserCapabilityEntry],
    budgets: SearchSessionBudgets,
    approved: bool = False,
) -> ManualChallengeInfo:
    reason_code = _reason_code_for_manual_route(
        explanation,
        inventory_by_id=inventory_by_id,
    )
    deadline = _deadline_for_manual_route(
        budgets=budgets,
        explanation=explanation,
        inventory_by_id=inventory_by_id,
    )
    budget_note = None
    if deadline is not None:
        budget_note = f"challenge budget/deadline is {deadline:g}s; no automatic extension"
    elif budgets.max_items is not None or budgets.max_sources is not None:
        budget_note = "session item/source budgets apply; manual route is not auto-executed"
    return ManualChallengeInfo(
        source_id=source_id,
        source_label=source_label,
        route_id=explanation.selected_capability_id,
        reason_code=reason_code,
        user_action_hint=_MANUAL_ACTION_HINTS[reason_code],
        requires_approval=True,
        approved=approved,
        deadline_seconds=deadline,
        budget_note=budget_note,
    )


def _is_manual_route(entry: SourceRoutePlanEntry) -> bool:
    if entry.status == "needs_manual":
        return True
    if entry.manual_challenge is not None:
        return True
    return entry.selected_group in _MANUAL_GROUPS


def _sync_needs_manual_ids(session: SearchSession) -> SearchSession:
    session.needs_manual_source_ids = tuple(
        item.source_id
        for item in session.route_plan
        if item.status == "needs_manual" and item.enabled
    )
    return session


def _runnable_source_ids(route_plan: Sequence[SourceRoutePlanEntry]) -> tuple[str, ...]:
    """Sources eligible for automatic pipeline run (excludes needs_manual)."""
    return tuple(
        item.source_id
        for item in route_plan
        if item.enabled
        and item.status not in {"skipped", "failed", "needs_manual"}
        and (not item.requires_approval or item.approved)
        and not _is_manual_route(item)
    )


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
                    source_kind=explanation.source_kind
                    or str(source.get("source_kind") or "")
                    or None,
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
        source_name = str(source.get("source_name") or "") or None
        manual_challenge: ManualChallengeInfo | None = None
        if explanation.selected_group in _MANUAL_GROUPS:
            status = "needs_manual"
            requires_approval = True
            manual_challenge = _build_manual_challenge(
                source_id=source_id,
                source_label=source_name,
                explanation=explanation,
                inventory_by_id=inventory_by_id,
                budgets=session.budgets,
            )
        elif explanation.selected_capability_id is None:
            status = "skipped"
            requires_approval = False

        if status == "needs_manual":
            reason = (
                f"manual challenge required ({manual_challenge.reason_code})"
                if manual_challenge is not None
                else "manual challenge required"
            )
        elif requires_approval:
            reason = "sensitive route requires explicit approval"
        elif explanation.selected_capability_id:
            reason = "route selected"
        else:
            reason = "no available route"

        route_plan.append(
            SourceRoutePlanEntry(
                source_id=source_id,
                source_kind=explanation.source_kind or str(source.get("source_kind") or "") or None,
                source_name=source_name,
                enabled=True,
                status=status,
                selected_capability_id=explanation.selected_capability_id,
                selected_group=explanation.selected_group,
                requires_approval=requires_approval,
                risk=cast(
                    "Any",
                    _risk_for_route(explanation, inventory_by_id=inventory_by_id),
                ),
                reason=reason,
                diagnostics=explanation.diagnostics,
                route_notes=explanation.notes,
                manual_challenge=manual_challenge,
            )
        )

    needs_approval = any(item.requires_approval and not item.approved for item in route_plan)
    session.route_plan = tuple(route_plan)
    session = _sync_needs_manual_ids(session)
    # Runnable auto-pipeline sources exclude needs_manual (HITL is not auto-bypass).
    session.selected_source_ids = _runnable_source_ids(route_plan)
    session.planned_at = _now()
    session.status = "awaiting_approval" if needs_approval else "planned"
    manual_count = len(session.needs_manual_source_ids)
    session.notes = (
        *(session.notes or ()),
        f"planned {len(route_plan)} source route(s)",
        "awaiting approval for sensitive routes"
        if needs_approval
        else "no sensitive approval required",
        f"{manual_count} source(s) need manual challenge handling"
        if manual_count
        else "no manual challenge routes",
    )
    session.provenance = {
        **(session.provenance or {}),
        "route_plan_generated_at": session.planned_at.isoformat(),
        "capability_inventory_status": inventory.status,
        "needs_manual_count": manual_count,
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
            updates: dict[str, Any] = {"approved": True}
            # HITL routes stay needs_manual after approval; consent != auto-bypass.
            if _is_manual_route(entry):
                updates["status"] = "needs_manual"
                if entry.manual_challenge is not None:
                    updates["manual_challenge"] = entry.manual_challenge.model_copy(
                        update={"approved": True}
                    )
                updates["reason"] = (
                    entry.reason or "manual challenge acknowledged; waiting for operator action"
                )
            updated_plan.append(entry.model_copy(update=updates))
        else:
            # Unapproved sensitive routes are skipped at run time.
            updates = {
                "approved": False,
                "status": "skipped",
                "reason": entry.reason or "sensitive route not approved",
            }
            if entry.manual_challenge is not None:
                updates["manual_challenge"] = entry.manual_challenge.model_copy(
                    update={"approved": False}
                )
            updated_plan.append(entry.model_copy(update=updates))

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
    session = _sync_needs_manual_ids(session)
    session.selected_source_ids = _runnable_source_ids(updated_plan)
    session.notes = (
        *(session.notes or ()),
        f"approval recorded; runnable sources={len(session.selected_source_ids)}",
        f"needs_manual sources={len(session.needs_manual_source_ids)} "
        "(not auto-executed; human challenge handling required)",
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
        # Preserve HITL routes; run outcomes must not pretend they were automated.
        if entry.status == "needs_manual" or _is_manual_route(entry):
            updated_plan.append(
                entry.model_copy(
                    update={
                        "status": "needs_manual",
                        "reason": entry.reason or "manual challenge still requires operator action",
                    }
                )
            )
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
    session = _sync_needs_manual_ids(session)
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
            "needs_manual_count": len(session.needs_manual_source_ids),
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
            msg = "sensitive routes require approve_search_session before run: " + ", ".join(
                pending[:10]
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
        session = _sync_needs_manual_ids(session)
        session.status = "completed"
        session.finished_at = _now()
        manual_n = len(session.needs_manual_source_ids)
        if manual_n:
            note = (
                f"no auto-runnable sources; {manual_n} source(s) remain needs_manual "
                "(human login/challenge required; not bypassed automatically)"
            )
        else:
            note = "no runnable sources after planning/approval"
        session.notes = (*(session.notes or ()), note)
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
        session = _sync_needs_manual_ids(session)
        manual_n = len(session.needs_manual_source_ids)
        complete_note = f"run completed with {len(session.result_refs)} result(s)"
        if manual_n:
            complete_note = (
                f"{complete_note}; {manual_n} source(s) still needs_manual (not auto-executed)"
            )
        session.notes = (*(session.notes or ()), complete_note)
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
        source_reasons = [r for r in (entry.reason, entry.error) if r]
        if entry.status == "skipped" and entry.requires_approval and not entry.approved:
            source_reasons.append("sensitive route was not approved")
        if entry.status == "needs_manual":
            source_reasons.append("source requires human-in-the-loop login/challenge handling")
            if entry.manual_challenge is not None:
                source_reasons.append(f"reason_code={entry.manual_challenge.reason_code}")
                if not entry.manual_challenge.approved:
                    source_reasons.append("manual challenge not yet approved by operator")
                else:
                    source_reasons.append(
                        "manual challenge approved as consent only; automatic bypass not performed"
                    )
        if entry.source_id in session.degraded_source_ids:
            source_reasons.append("source marked degraded after run")
        # Refresh route diagnostics for currency without executing browsers.
        explanation = await runner.explain_browser_route(session.tenant_id, source_id)
        public_route = explanation_to_public_dict(explanation)
        evidence: dict[str, object] = {
            "selected_capability_id": entry.selected_capability_id,
            "selected_group": entry.selected_group,
            "requires_approval": entry.requires_approval,
            "approved": entry.approved,
            "route": public_route,
            "rejected_summary": dict(session.rejected_summary),
        }
        source_notes = list(entry.route_notes) + list(explanation.notes)
        if entry.manual_challenge is not None:
            # Public-safe HITL payload only (no tenant/user ids, cookies, tokens).
            evidence["manual_challenge"] = entry.manual_challenge.model_dump(mode="json")
            source_notes.append(entry.manual_challenge.user_action_hint)
            if entry.manual_challenge.budget_note:
                source_notes.append(entry.manual_challenge.budget_note)
        return SearchSessionExplanation(
            session_id=session_id,
            target_type="source",
            target_id=source_id,
            status=entry.status,
            reasons=tuple(source_reasons) or ("no explicit rejection reason recorded",),
            diagnostics=entry.diagnostics or explanation.diagnostics,
            notes=tuple(source_notes),
            evidence=evidence,
        )

    assert job_id is not None
    ref = next((item for item in session.result_refs if item.job_id == job_id), None)
    lineage = await runner.get_job_lineage(job_id, tenant_id=session.tenant_id)
    reasons: list[str] = []
    notes: list[str] = []
    job_evidence: dict[str, object] = {"in_session_results": ref is not None}
    if ref is None:
        reasons.append("job is not in session result set")
        notes.append("job may have been filtered by existing relevance/evidence pipeline")
    else:
        if ref.routing_decision:
            reasons.append(f"routing_decision={ref.routing_decision}")
        if ref.best_score is not None:
            reasons.append(f"best_score={ref.best_score}")
        job_evidence["result_ref"] = ref.model_dump(mode="json")
    if lineage is not None:
        # Authenticated explain still must not dump tenant/private ids or raw
        # lineage/provenance blobs into session-facing evidence.
        public_lineage = _public_lineage_evidence(lineage)
        job_evidence["lineage"] = public_lineage
        source_name = public_lineage.get("source_name")
        source_kind = public_lineage.get("source_kind")
        if source_name:
            reasons.append(f"source_name={source_name}")
        if source_kind:
            reasons.append(f"source_kind={source_kind}")
        notes.append(
            "lineage evidence is trimmed: source kind/name/stages/timestamps only; "
            "tenant/user/raw_item/run ids and full provenance are omitted"
        )
    if session.rejected_summary:
        job_evidence["session_rejected_summary"] = dict(session.rejected_summary)
        notes.append("session rejected_summary aggregates pipeline drop reasons for the run")
    return SearchSessionExplanation(
        session_id=session_id,
        target_type="job",
        target_id=job_id,
        status="selected" if ref is not None else "not_selected",
        reasons=tuple(reasons) or ("no additional decision evidence available",),
        notes=tuple(notes),
        evidence=job_evidence,
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
