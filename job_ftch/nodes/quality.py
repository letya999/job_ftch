"""Job scoring and minimum usefulness validation."""

from __future__ import annotations

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    JobRecord,
    JobReviewReason,
    JobValidationRejectionReason,
    MatchDecision,
)


class QualityScoringNode:
    def __init__(self, *, review_threshold: float = 0.6) -> None:
        self._review_threshold = review_threshold

    async def process(self, item: JobRecord) -> JobRecord | None:
        score = 0.0
        score += 0.2 if item.title else 0.0
        score += 0.2 if item.company else 0.0
        score += 0.15 if item.canonical_url else 0.0
        score += 0.1 if item.location else 0.0
        score += 0.1 if item.work_mode.value != "unknown" else 0.0
        score += 0.1 if item.compensation else 0.0
        score += 0.15 if len(item.description or "") >= 120 else 0.05
        score += 0.1 * (item.relevance_score or 0.0)
        score += 0.05 if item.skills_explicit else 0.0
        score -= min(0.2, 0.05 * len(item.risk_signals))
        score = min(1.0, round(score, 2))
        review_reasons = list(item.review_reasons)
        if (
            score < self._review_threshold
            and JobReviewReason.LOW_QUALITY_SCORE.value not in review_reasons
        ):
            review_reasons.append(JobReviewReason.LOW_QUALITY_SCORE.value)
        return item.model_copy(
            update={"quality_score": score, "review_reasons": tuple(review_reasons)}
        )


class JobValidationNode:
    def __init__(
        self,
        *,
        min_quality_score: float = 0.25,
        min_relevance_score: float = 0.25,
    ) -> None:
        self._min_quality_score = min_quality_score
        self._min_relevance_score = min_relevance_score

    async def process(self, item: JobRecord) -> JobRecord | None:
        if item.title is None and item.company is None and item.canonical_url is None:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_MISSING_CORE_FIELDS,
                details="Job is missing every core locator field after normalization.",
                item=item,
                stage=self.__class__.__name__,
            )
        if (item.relevance_score or 0.0) < self._min_relevance_score:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job relevance score is below the minimum threshold.",
                item=item,
                stage=self.__class__.__name__,
            )
        if item.profile_scores and all(
            score.decision is MatchDecision.REJECT for score in item.profile_scores
        ):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="All active search profiles rejected this vacancy.",
                item=item,
                stage=self.__class__.__name__,
            )
        if (item.quality_score or 0.0) < self._min_quality_score:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_TOO_LOW_QUALITY,
                details="Job quality score is below the minimum threshold.",
                item=item,
                stage=self.__class__.__name__,
            )
        return item
