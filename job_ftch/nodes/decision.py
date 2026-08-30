"""Single policy owner for terminal routing decisions."""

from __future__ import annotations

from dataclasses import dataclass

from job_ftch.domain import (
    AssessedJob,
    ClaimAssessment,
    ClaimKind,
    DecisionResult,
    EvidencePolarity,
    EvidenceProvenance,
    MatchDecision,
    PostType,
    WorkState,
)


@dataclass(frozen=True)
class DecisionPolicy:
    # The aggregator's prior is deliberately conservative; one strong
    # classifier signal should clear jobness without requiring an unnecessary
    # second expensive resolver.
    job_accept_belief: float = 0.55
    job_accept_certainty: float = 0.50
    relevance_accept_belief: float = 0.65
    relevance_accept_certainty: float = 0.55
    relevance_reject_belief: float = 0.25
    relevance_reject_certainty: float = 0.75
    hard_constraint_veto_belief: float = 0.25
    hard_constraint_veto_certainty: float = 0.75
    risk_veto_belief: float = 0.75
    risk_veto_certainty: float = 0.65
    freshness_min_certainty: float = 0.45
    defer_unknown_critical: bool = True


class DecisionNode:
    """Pure `AssessedJob -> DecisionResult` policy boundary.

    This class is the only place allowed to select a routing lane. Producers
    must never update `JobRecord.routing_decision` themselves.
    """

    def __init__(self, policy: DecisionPolicy | None = None) -> None:
        self._policy = policy or DecisionPolicy()
        self._audit_mode = False

    def enable_audit_mode(self) -> None:
        """Keep personal MCP cards reviewable until full extraction completes."""
        self._audit_mode = True

    async def process(self, item: AssessedJob) -> DecisionResult:
        if self._audit_mode:
            return self._accept(item, [], "personal_audit_full_extraction")
        assessments = item.assessments
        reasons: list[str] = []
        if item.degradation_reasons:
            reasons.extend(item.degradation_reasons)
            return self._deferred(item, reasons)

        if self._has_veto(assessments, ClaimKind.HARD_CONSTRAINT):
            return self._reject(item, reasons, "confirmed_hard_constraint")
        if self._has_veto(assessments, ClaimKind.RISK):
            return self._reject(item, reasons, "high_risk")

        jobness = self._find(assessments, ClaimKind.IS_JOB, "vacancy")
        if jobness is None:
            return self._deferred(item, reasons, "jobness_unknown")
        if not self._is_confident_positive(
            jobness, self._policy.job_accept_belief, self._policy.job_accept_certainty
        ):
            if self._is_confident_negative(
                jobness,
                self._policy.relevance_reject_belief,
                self._policy.relevance_reject_certainty,
            ):
                return self._reject(item, reasons, "confirmed_not_job")
            return self._deferred(item, reasons, "jobness_unknown")

        freshness = [
            assessment for assessment in assessments if assessment.claim is ClaimKind.FRESHNESS
        ]
        if (
            freshness
            and max(assessment.certainty for assessment in freshness)
            < self._policy.freshness_min_certainty
        ):
            return self._deferred(item, reasons, "freshness_unknown")

        relevance = [
            assessment
            for assessment in assessments
            if assessment.claim is ClaimKind.PROFILE_RELEVANCE
            and assessment.subject == "profile_relevance"
        ]
        if not relevance:
            # A source without configured profiles is reviewable immediately;
            # defer only when profiles exist but their evidence is incomplete.
            if not item.record.profile_scores:
                return self._review(item, reasons, "profile_relevance_unconfigured")
            return self._deferred(item, reasons, "profile_relevance_unknown")

        # Compact LLM support has already passed the responsibility-first,
        # cited evidence contract. A lexical anti-pattern is only a weak
        # conflict signal, so it must not turn that explicit result into a
        # review. A cited LLM contradiction still wins and leaves the item in
        # the normal aggregate path below.
        cited_llm_support = any(
            atom.claim is ClaimKind.PROFILE_RELEVANCE
            and atom.provenance is EvidenceProvenance.LLM
            and atom.polarity is EvidencePolarity.SUPPORTS
            and atom.evidence_ref.startswith("llm:relevance_evidence:")
            for atom in item.evidence
        )
        strong_llm_support = any(
            atom.claim is ClaimKind.PROFILE_RELEVANCE
            and atom.provenance is EvidenceProvenance.LLM
            and atom.polarity is EvidencePolarity.SUPPORTS
            and atom.strength >= 0.8
            and atom.reliability >= 0.8
            for atom in item.evidence
        )
        cited_llm_contradiction = any(
            atom.claim is ClaimKind.PROFILE_RELEVANCE
            and atom.provenance is EvidenceProvenance.LLM
            and atom.polarity is EvidencePolarity.CONTRADICTS
            for atom in item.evidence
        )
        if not cited_llm_contradiction:
            if cited_llm_support:
                return self._accept(item, reasons, "profile_relevance_confirmed_by_cited_llm")
            if strong_llm_support:
                return self._accept(item, reasons, "profile_relevance_confirmed_by_strong_llm")

        accepted = [
            assessment
            for assessment in relevance
            if self._is_confident_positive(
                assessment,
                self._policy.relevance_accept_belief,
                self._policy.relevance_accept_certainty,
            )
        ]
        if accepted:
            accepted_profiles = {assessment.profile_id for assessment in accepted}
            llm_atoms = tuple(
                atom
                for atom in item.evidence
                if atom.claim is ClaimKind.PROFILE_RELEVANCE
                and atom.provenance is EvidenceProvenance.LLM
                and atom.profile_id in accepted_profiles
            )
            llm_support = any(atom.polarity is EvidencePolarity.SUPPORTS for atom in llm_atoms)
            if not llm_support:
                # The judge ran and supplied non-supporting evidence.  This is
                # an evidence conflict, not unfinished work: keep it visible
                # for review.  DEFERRED is reserved for the genuinely absent
                # LLM evidence/provider path so a retry can add information.
                if llm_atoms:
                    return self._review(item, reasons, "profile_relevance_uncertain")
                return self._deferred(item, reasons, "relevance_llm_required")
            return self._accept(item, reasons, "profile_relevance_confirmed")

        if all(
            self._is_confident_negative(
                assessment,
                self._policy.relevance_reject_belief,
                self._policy.relevance_reject_certainty,
            )
            for assessment in relevance
        ):
            return self._reject(item, reasons, "all_profiles_rejected")
        return self._review(item, reasons, "profile_relevance_uncertain")

    def _has_veto(self, assessments: tuple[ClaimAssessment, ...], claim: ClaimKind) -> bool:
        for assessment in assessments:
            if assessment.claim is claim:
                if claim is ClaimKind.RISK:
                    if self._is_confident_positive(
                        assessment,
                        self._policy.risk_veto_belief,
                        self._policy.risk_veto_certainty,
                    ):
                        return True
                elif self._is_confident_negative(
                    assessment,
                    self._policy.hard_constraint_veto_belief,
                    self._policy.hard_constraint_veto_certainty,
                ):
                    return True
        return False

    @staticmethod
    def _find(
        assessments: tuple[ClaimAssessment, ...], claim: ClaimKind, subject: str
    ) -> ClaimAssessment | None:
        return next(
            (
                assessment
                for assessment in assessments
                if assessment.claim is claim and assessment.subject == subject
            ),
            None,
        )

    @staticmethod
    def _is_confident_positive(
        assessment: ClaimAssessment, belief: float, certainty: float
    ) -> bool:
        return assessment.belief_true >= belief and assessment.certainty >= certainty

    @staticmethod
    def _is_confident_negative(
        assessment: ClaimAssessment, belief: float, certainty: float
    ) -> bool:
        return assessment.belief_true <= belief and assessment.certainty >= certainty

    @staticmethod
    def _with_decision(item: AssessedJob, decision: MatchDecision) -> AssessedJob:
        return item.model_copy(
            update={
                "record": item.record.model_copy(update={"routing_decision": decision}),
            }
        )

    def _accept(self, item: AssessedJob, reasons: list[str], reason: str) -> DecisionResult:
        # ACCEPT is the single authoritative statement that this record is both
        # a vacancy and relevant.  Downstream adapters must not have to repair
        # the cheap extraction draft's deliberately UNKNOWN post type.
        updated = item.model_copy(
            update={
                "record": item.record.model_copy(
                    update={
                        "routing_decision": MatchDecision.ACCEPT,
                        "post_type": PostType.JOB_POSTING,
                    }
                ),
            }
        )
        return DecisionResult(
            assessed_job=updated,
            routing_decision=MatchDecision.ACCEPT,
            work_state=WorkState.TERMINAL,
            reasons=tuple((*reasons, reason)),
        )

    def _reject(self, item: AssessedJob, reasons: list[str], reason: str) -> DecisionResult:
        updated = self._with_decision(item, MatchDecision.REJECT)
        return DecisionResult(
            assessed_job=updated,
            routing_decision=MatchDecision.REJECT,
            work_state=WorkState.TERMINAL,
            reasons=tuple((*reasons, reason)),
        )

    def _review(self, item: AssessedJob, reasons: list[str], reason: str) -> DecisionResult:
        updated = self._with_decision(item, MatchDecision.REVIEW)
        return DecisionResult(
            assessed_job=updated,
            routing_decision=MatchDecision.REVIEW,
            work_state=WorkState.TERMINAL,
            reasons=tuple((*reasons, reason)),
        )

    def _deferred(
        self, item: AssessedJob, reasons: list[str], reason: str = "evidence_missing"
    ) -> DecisionResult:
        return DecisionResult(
            assessed_job=item,
            routing_decision=None,
            work_state=WorkState.DEFERRED,
            reasons=tuple((*reasons, reason)),
        )
