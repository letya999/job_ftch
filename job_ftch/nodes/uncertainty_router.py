"""Cheap three-zone routing before the expensive relevance judge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.graph.params import float_param

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


class UncertaintyRouterNode:
    """Annotate clear reject/accept zones and reserve LLM for disagreement.

    This node never writes the terminal routing field. The existing relevance
    node and terminal routing node remain the sole owners of that decision.
    """

    def __init__(self, *, low_threshold: float = 0.20, high_threshold: float = 0.50) -> None:
        self._low = low_threshold
        self._high = high_threshold

    def configure_graph_params(self, params: dict[str, object]) -> None:
        self._low = float_param(params, "low_threshold", self._low)
        self._high = float_param(params, "high_threshold", self._high)

    async def process(self, item: JobRecord) -> JobRecord:
        score = (item.metadata or {}).get("parallel_final_score", item.relevance_score or 0.0)
        value = float(score or 0.0)
        quality = item.quality_score
        risk = str(getattr(item, "risk_level", "")).casefold()
        negative = value < self._low and (quality is None or quality < self._high)
        positive = value >= self._high and (quality is None or quality >= self._low)
        if risk in {"high", "critical"}:
            negative = True
            positive = False
        if negative:
            zone = "consistent_negative"
            recommendation = "reject"
        elif positive:
            zone = "consistent_positive"
            recommendation = "accept"
        else:
            zone = "disagreement"
            recommendation = None
        metadata = {
            **item.metadata,
            "uncertainty_zone": zone,
            "needs_llm_review": zone == "disagreement",
            "uncertainty_recommendation": recommendation,
        }
        return item.model_copy(update={"metadata": metadata})
