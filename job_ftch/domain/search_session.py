"""Resume-driven search session contracts.

High-level workflow state for "given a profile, plan sources, approve sensitive
routes, run existing pipeline/search primitives, and explain degraded/rejected
outcomes". Privacy: session state may hold tenant/user/profile identifiers and
route diagnostics, but never resume body text, cookies, tokens, proxy URLs, or
browser profile paths.

Manual challenge (HITL) contract: when a source route needs login/challenge or
operator help, the route is reported as ``needs_manual`` with public-safe
``ManualChallengeInfo``. Approval records consent/budget acknowledgment only;
it does not claim automated CAPTCHA/login bypass.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic evaluates annotations at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Runtime imports required for Pydantic field evaluation (not TYPE_CHECKING-only).
from job_ftch.domain.browser_capability_inventory import (  # noqa: TC001
    CapabilityGroup,
    CapabilityRisk,
    RouteCapabilityDiagnostic,
)

SearchSessionStatus = Literal[
    "created",
    "planned",
    "awaiting_approval",
    "approved",
    "running",
    "completed",
    "cancelled",
    "failed",
]

SourceSessionStatus = Literal[
    "pending",
    "checked",
    "skipped",
    "failed",
    "degraded",
    "needs_manual",
    "no_results",
]

ManualChallengeReasonCode = Literal[
    "login_required",
    "challenge_required",
    "captcha_required",
    "auth_wall",
    "manual_route_selected",
    "operator_approval_required",
]

# Fields allowed on public manual-challenge diagnostics (contract denylist is
# documented in DEFAULT_SESSION_PRIVACY_NOTES and ManualChallengeInfo docstring).
MANUAL_CHALLENGE_PUBLIC_FIELDS: tuple[str, ...] = (
    "source_id",
    "source_label",
    "route_id",
    "reason_code",
    "user_action_hint",
    "requires_approval",
    "approved",
    "deadline_seconds",
    "budget_note",
)

DEFAULT_SESSION_PRIVACY_NOTES: tuple[str, ...] = (
    "session stores tenant/user/profile identifiers only; resume body is not stored on the session",
    "route diagnostics are public-safe and must not include cookies, tokens, proxy URLs, or profile paths",
    "manual_challenge diagnostics allow only source_id, source_label, route_id, reason_code, "
    "user_action_hint, deadline/budget flags; never cookies, tokens, proxy endpoints, browser "
    "profile paths, raw HTML/traces, private tenant/user ids, or resume text",
    "public/explain payloads scrub sensitive-looking free-text values in notes/provenance/"
    "evidence/reasons (proxy URLs, cookie/token headers, browser paths, HTML, internal endpoints) "
    "without over-redacting route/source/capability ids",
    "job explain lineage evidence is trimmed to source kind/name/stages/timestamps and presence "
    "flags; tenant/user/raw_item/run ids and full provenance blobs are omitted",
    "needs_manual means human/operator action is required; approval is not automatic bypass",
    "results reuse existing pipeline/search decisions; no separate relevance logic",
)


class SearchSessionBudgets(BaseModel):
    """Optional execution budgets for a search session run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int | None = Field(default=None, ge=1)
    max_sources: int | None = Field(default=None, ge=1)
    deadline_seconds: float | None = Field(default=None, ge=0.0)
    result_limit: int = Field(default=20, ge=1, le=100)


class SearchSessionApproval(BaseModel):
    """User/operator approvals for sensitive routes and budgets.

    Approval acknowledges cost/risk and budgets. For ``needs_manual`` routes it
    records operator consent to proceed with a human-in-the-loop challenge; it
    does not store credentials or mean the system can solve login/CAPTCHA alone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved_source_ids: tuple[str, ...] = ()
    approved_capability_ids: tuple[str, ...] = ()
    approve_all_sensitive: bool = False
    note: str | None = None
    approved_at: datetime | None = None


class ManualChallengeInfo(BaseModel):
    """Public-safe human-in-the-loop challenge diagnostics for one source route.

    Allowed fields only. Must never carry cookies, tokens, proxy endpoints,
    browser profile/executable paths, raw HTML/traces, private tenant/user ids,
    or resume text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    source_label: str | None = None
    route_id: str | None = None
    reason_code: ManualChallengeReasonCode
    user_action_hint: str
    requires_approval: bool = True
    approved: bool = False
    deadline_seconds: float | None = Field(default=None, ge=0.0)
    budget_note: str | None = None


class SourceRoutePlanEntry(BaseModel):
    """Per-source route plan and execution status within a session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    source_kind: str | None = None
    source_name: str | None = None
    enabled: bool = True
    status: SourceSessionStatus = "pending"
    selected_capability_id: str | None = None
    selected_group: CapabilityGroup | None = None
    requires_approval: bool = False
    approved: bool = False
    risk: CapabilityRisk | None = None
    reason: str | None = None
    diagnostics: tuple[RouteCapabilityDiagnostic, ...] = ()
    route_notes: tuple[str, ...] = ()
    error: str | None = None
    manual_challenge: ManualChallengeInfo | None = None


class SearchResultRef(BaseModel):
    """Reference to a job produced or ranked for a session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    group_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    title: str | None = None
    best_score: float | None = Field(default=None, ge=0.0, le=1.0)
    routing_decision: str | None = None


class SearchSessionExplanation(BaseModel):
    """Explanation for a rejected/degraded source or job within a session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    target_type: Literal["source", "job"]
    target_id: str
    status: str | None = None
    reasons: tuple[str, ...] = ()
    diagnostics: tuple[RouteCapabilityDiagnostic, ...] = ()
    notes: tuple[str, ...] = ()
    evidence: dict[str, object] = Field(default_factory=dict)


class SearchSession(BaseModel):
    """Persisted search session state for resume-driven workflows."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    tenant_id: str
    user_id: str | None = None
    profile_id: str | None = None
    status: SearchSessionStatus = "created"
    source_scope: tuple[str, ...] = ()
    selected_source_ids: tuple[str, ...] = ()
    route_plan: tuple[SourceRoutePlanEntry, ...] = ()
    approval: SearchSessionApproval | None = None
    budgets: SearchSessionBudgets = Field(default_factory=SearchSessionBudgets)
    run_ids: tuple[str, ...] = ()
    result_refs: tuple[SearchResultRef, ...] = ()
    rejected_summary: dict[str, int] = Field(default_factory=dict)
    degraded_source_ids: tuple[str, ...] = ()
    needs_manual_source_ids: tuple[str, ...] = ()
    error: str | None = None
    cancel_requested: bool = False
    created_at: datetime
    updated_at: datetime
    planned_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notes: tuple[str, ...] = ()
    privacy_notes: tuple[str, ...] = DEFAULT_SESSION_PRIVACY_NOTES
    provenance: dict[str, object] = Field(default_factory=dict)
