"""Job scoring and minimum usefulness validation."""

from __future__ import annotations

import structlog

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.graph.params import float_param
from job_ftch.domain import (
    JobRecord,
    JobReviewReason,
    JobValidationRejectionReason,
    MatchDecision,
)

logger = structlog.get_logger(__name__)


class QualityScoringNode:
    def __init__(self, *, review_threshold: float = 0.6) -> None:
        self._review_threshold = review_threshold

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "review_threshold" in params:
            self._review_threshold = float_param(params, "review_threshold", self._review_threshold)

    async def process(self, item: JobRecord) -> JobRecord | None:
        score = 0.0
        score += 0.2 if item.title else 0.0
        score += 0.2 if item.company else 0.0
        score += 0.15 if item.canonical_url else 0.0
        score += 0.1 if item.location else 0.0
        score += 0.1 if item.work_mode.value != "unknown" else 0.0
        score += 0.1 if item.compensation else 0.0
        score += 0.15 if len(item.description or "") >= 120 else 0.05
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
        llm_band_floor: float | None = None,
        enforce_policy: bool = True,
    ) -> None:
        self._min_quality_score = min_quality_score
        self._min_relevance_score = min_relevance_score
        self._llm_band_floor = llm_band_floor
        self._enforce_policy = enforce_policy

    def configure_graph_params(self, params: dict[str, object]) -> None:
        for name, attr in (
            ("min_quality_score", "_min_quality_score"),
            ("min_relevance_score", "_min_relevance_score"),
        ):
            if name in params:
                setattr(self, attr, float_param(params, name, getattr(self, attr)))
        if "llm_band_floor" in params:
            self._llm_band_floor = float_param(
                params, "llm_band_floor", self._llm_band_floor or 0.0
            )
        if "enforce_policy" in params:
            self._enforce_policy = bool(params["enforce_policy"])

    async def process(self, item: JobRecord) -> JobRecord | None:
        if item.title is None and item.company is None and item.canonical_url is None:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_MISSING_CORE_FIELDS,
                details="Job is missing every core locator field after normalization.",
                item=item,
                stage=self.__class__.__name__,
            )
        if not self._enforce_policy:
            # Runtime policy is owned by EvidenceDecisionNode. Keep this node
            # structural-only in the production graph; the legacy default is
            # retained for direct callers during migration.
            return item
        pfs = (item.metadata or {}).get("parallel_final_score")
        in_band = (
            self._llm_band_floor is not None
            and isinstance(pfs, (int, float))
            and pfs >= self._llm_band_floor
        )
        if in_band:
            return item

        # Graceful degradation: when the pipeline expected parallel scoring
        # (llm_band_floor set) but parallel_final_score is missing, the scorer
        # is unavailable. Do not mass-drop on the relevance floor in that case
        # — keep the item (the LLM judge downstream still decides) and warn so
        # the missing-scorer condition is loud rather than a silent mass-drop.
        scorer_unavailable = self._llm_band_floor is not None and pfs is None
        if scorer_unavailable:
            llm_decision = (item.metadata or {}).get("_llm_relevance", {}).get("decision")
            if llm_decision != "accept":
                logger.warning(
                    "job_validation_no_pfs",
                    item_id=getattr(item, "stable_id", None),
                )
        elif (item.relevance_score or 0.0) < self._min_relevance_score:
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
