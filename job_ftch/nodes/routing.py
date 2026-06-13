"""Routing logic for match decisions based on profile scores and quality."""

from __future__ import annotations

from job_ftch.domain import JobRecord, JobReviewReason, MatchDecision

ROUTING_REASON_CODES = {
    "PROFILE_MATCH": "profile_match",
    "PROFILE_REVIEW": "profile_review",
    "PROFILE_REJECT": "profile_reject",
    "LOW_QUALITY": "low_quality",
    "NO_PROFILE_MATCH": "no_profile_match",
}


class RoutingNode:
    """
    Stage[JobRecord, JobRecord] — final routing decision before sinks.
    Sets routing_decision to ACCEPT, REVIEW, or REJECT.
    """

    def __init__(
        self,
        *,
        accept_threshold: float = 0.85,
        review_threshold: float = 0.5,
        quality_override_threshold: float = 0.6,
    ) -> None:
        self._accept_threshold = accept_threshold
        self._review_threshold = review_threshold
        self._quality_override_threshold = quality_override_threshold

    async def process(self, job: JobRecord) -> JobRecord:
        decision = MatchDecision.REJECT
        reasons = list(job.review_reasons)

        if not job.profile_scores:
            decision = MatchDecision.REJECT
            if ROUTING_REASON_CODES["NO_PROFILE_MATCH"] not in reasons:
                reasons.append(ROUTING_REASON_CODES["NO_PROFILE_MATCH"])
        else:
            # Find the best profile score
            best_profile = max(job.profile_scores, key=lambda s: s.final_score)

            if best_profile.final_score >= self._accept_threshold:
                decision = MatchDecision.ACCEPT
                reasons.append(f"{ROUTING_REASON_CODES['PROFILE_MATCH']}:{best_profile.profile_id}")
            elif best_profile.final_score >= self._review_threshold:
                decision = MatchDecision.REVIEW
                reasons.append(
                    f"{ROUTING_REASON_CODES['PROFILE_REVIEW']}:{best_profile.profile_id}"
                )
            else:
                decision = MatchDecision.REJECT
                reasons.append(
                    f"{ROUTING_REASON_CODES['PROFILE_REJECT']}:{best_profile.profile_id}"
                )

        # Quality override: if quality is low, move to REVIEW even if profile matched well
        if (
            decision == MatchDecision.ACCEPT
            and (job.quality_score or 0.0) < self._quality_override_threshold
        ):
            decision = MatchDecision.REVIEW
            if JobReviewReason.LOW_QUALITY_SCORE.value not in reasons:
                reasons.append(JobReviewReason.LOW_QUALITY_SCORE.value)

        return job.model_copy(
            update={
                "routing_decision": decision,
                "review_reasons": tuple(reasons),
            }
        )
