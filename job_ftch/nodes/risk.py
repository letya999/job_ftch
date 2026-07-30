"""Risk scoring separated from relevance scoring."""

from __future__ import annotations

from job_ftch.application.graph.params import float_param
from job_ftch.domain import JobRecord, RiskLevel  # noqa: TC001


class RiskScoringNode:
    def __init__(self, *, review_threshold: float = 0.45) -> None:
        self._review_threshold = review_threshold

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "review_threshold" in params:
            self._review_threshold = float_param(params, "review_threshold", self._review_threshold)

    async def process(self, item: JobRecord) -> JobRecord | None:
        lowered = "\n".join(
            part for part in (item.title or "", item.company or "", item.description) if part
        ).casefold()
        signals: list[str] = list(item.risk_signals)

        if any(token in lowered for token in ("crypto", "nft", "casino", "betting", "mlm")):
            signals.append("suspicious_domain")
        if "telegram" in lowered and "dm" in lowered and item.canonical_url is None:
            signals.append("contact_only_apply_flow")
        if len(item.description or "") < 80:
            signals.append("low_information_density")

        risk_score = min(1.0, round(len(set(signals)) * 0.2, 2))
        risk_level = RiskLevel.LOW
        if risk_score >= 0.75:
            risk_level = RiskLevel.HIGH
        elif risk_score >= 0.4:
            risk_level = RiskLevel.MEDIUM
        review_reasons = list(item.review_reasons)
        if risk_score >= self._review_threshold and "high_risk_signals" not in review_reasons:
            review_reasons.append("high_risk_signals")

        metadata = {
            **item.metadata,
            "risk_score": risk_score,
        }
        return item.model_copy(
            update={
                "risk_signals": tuple(sorted(set(signals))),
                "risk_score": risk_score,
                "risk_level": risk_level,
                "review_reasons": tuple(review_reasons),
                "metadata": metadata,
            }
        )
