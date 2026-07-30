"""Pure terminal policy table for job delivery lanes (ADR-058)."""

from __future__ import annotations

from job_ftch.domain import JobRecord, JobStatus, MatchDecision, RiskLevel


def apply_terminal_veto(
    job: JobRecord, decision: MatchDecision, reasons: list[str]
) -> MatchDecision:
    """Apply non-negotiable policy axes after relevance scoring."""
    if job.status in {JobStatus.FILLED, JobStatus.EXPIRED, JobStatus.DELISTED}:
        reasons.append("freshness_not_active")
        return MatchDecision.REJECT
    if (
        decision is MatchDecision.ACCEPT
        and job.metadata.get("freshness_evidence_state") == "missing"
    ):
        reasons.append("freshness_evidence_missing")
        return MatchDecision.REVIEW
    if job.risk_level is RiskLevel.HIGH:
        reasons.append("high_risk_veto")
        return MatchDecision.REJECT
    evidence = job.metadata.get("hard_filter_evidence", ())
    if any(str(entry).startswith("blocked_company:") for entry in evidence):
        reasons.append("hard_constraint_blocked_company")
        return MatchDecision.REJECT
    if job.metadata.get("budget_outcome") == "deferred":
        reasons.append("budget_deferred")
        return MatchDecision.REVIEW
    selected_profile = job.best_profile_id
    constraint_states = job.metadata.get("hard_constraint_states", {})
    selected_states = (
        constraint_states.get(selected_profile, {}) if isinstance(constraint_states, dict) else {}
    )
    if decision is MatchDecision.ACCEPT and isinstance(selected_states, dict):
        if any(state == "contradicted" for state in selected_states.values()):
            reasons.append("hard_constraint_contradicted")
            return MatchDecision.REJECT
        if any(state == "unknown" for state in selected_states.values()):
            reasons.append("hard_constraint_unknown")
            return MatchDecision.REVIEW
    if decision is MatchDecision.ACCEPT and any(
        str(entry).startswith("language_not_allowed:") for entry in evidence
    ):
        reasons.append("hard_constraint_unknown_language")
        return MatchDecision.REVIEW
    return decision
