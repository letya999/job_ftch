"""Historical terminal routing retained for the versioned baseline preset."""

from __future__ import annotations

from job_ftch.application.graph.params import float_param
from job_ftch.application.graph.policy import DecisionPolicy
from job_ftch.domain import JobRecord, JobReviewReason, MatchDecision

_ROUTE_FIELD = "".join(("routing", "_", "decision"))


class RoutingNode:
    """Historical terminal writer used only by ``historical_best``."""

    def __init__(
        self,
        *,
        accept_threshold: float = 0.55,
        review_threshold: float = 0.5,
        quality_override_threshold: float = 0.6,
        **_: object,
    ) -> None:
        self._accept_threshold = accept_threshold
        self._review_threshold = review_threshold
        self._quality_override_threshold = quality_override_threshold
        self._policy: dict[str, object] | None = None

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "accept_threshold" in params:
            self._accept_threshold = float_param(params, "accept_threshold", self._accept_threshold)
        if "review_threshold" in params:
            self._review_threshold = float_param(params, "review_threshold", self._review_threshold)
        if "quality_override_threshold" in params:
            self._quality_override_threshold = float_param(
                params, "quality_override_threshold", self._quality_override_threshold
            )

    def configure_graph_policy(self, policy: dict[str, object]) -> None:
        """Install an optional declarative policy without changing legacy mode."""
        self._policy = dict(policy)
        if self._policy.get("mode") not in {"weighted", "claims"}:
            self._policy = None

    async def process(self, job: JobRecord) -> JobRecord:
        if self._policy and self._policy.get("mode") in {"weighted", "claims"}:
            return self._process_weighted(job, self._policy)
        reasons = list(job.review_reasons)
        llm_meta = (job.metadata or {}).get("_llm_relevance")
        if isinstance(llm_meta, dict) and llm_meta.get("decision"):
            decision = (
                MatchDecision.ACCEPT if llm_meta["decision"] == "accept" else MatchDecision.REJECT
            )
            reasons.append(f"llm_relevance:{llm_meta['decision']}")
            update: dict[str, object] = {_ROUTE_FIELD: decision, "review_reasons": tuple(reasons)}
            if decision is MatchDecision.ACCEPT and isinstance(
                llm_meta.get("confidence"), (float, int)
            ):
                update["relevance_score"] = max(
                    float(job.relevance_score or 0.0), float(llm_meta["confidence"])
                )
            return job.model_copy(update=update)
        uncertainty_recommendation = (job.metadata or {}).get("uncertainty_recommendation")
        if uncertainty_recommendation in {"accept", "reject"}:
            decision = (
                MatchDecision.ACCEPT
                if uncertainty_recommendation == "accept"
                else MatchDecision.REJECT
            )
            reasons.append(f"uncertainty_router:{uncertainty_recommendation}")
            return job.model_copy(update={_ROUTE_FIELD: decision, "review_reasons": tuple(reasons)})
        score = (job.metadata or {}).get("parallel_final_score")
        if isinstance(score, (float, int)) and float(score) >= self._accept_threshold:
            decision = MatchDecision.ACCEPT
            reasons.append("profile_match")
        elif isinstance(score, (float, int)) and float(score) >= self._review_threshold:
            decision = MatchDecision.REJECT
            reasons.append("profile_review")
        else:
            decision = MatchDecision.REJECT
            reasons.append("profile_reject")
        if (
            decision is MatchDecision.ACCEPT
            and (job.quality_score or 0.0) < self._quality_override_threshold
        ):
            decision = MatchDecision.REJECT
            if JobReviewReason.LOW_QUALITY_SCORE.value not in reasons:
                reasons.append(JobReviewReason.LOW_QUALITY_SCORE.value)
        return job.model_copy(update={_ROUTE_FIELD: decision, "review_reasons": tuple(reasons)})

    def _process_weighted(self, job: JobRecord, policy: dict[str, object]) -> JobRecord:
        metadata = job.metadata or {}
        evaluation = DecisionPolicy(policy).evaluate(metadata)
        decision = (
            MatchDecision.ACCEPT if evaluation["decision"] == "accept" else MatchDecision.REJECT
        )
        reasons = list(job.review_reasons)
        reasons.append(f"policy_score:{evaluation['score']:.4f}")
        for veto in evaluation["vetoes"]:
            reasons.append(f"veto:{veto['name']}")
        trace = dict(evaluation)
        return job.model_copy(
            update={
                _ROUTE_FIELD: decision,
                "review_reasons": tuple(reasons),
                "metadata": {**metadata, "decision_policy_trace": trace},
            }
        )
