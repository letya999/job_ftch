"""Bounded second opinion for items the primary LLM explicitly marked REVIEW."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from job_ftch.application.graph.params import float_param, int_param
from job_ftch.domain import JobRecord, MatchDecision  # noqa: TC001

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider


class ReviewResolution(BaseModel):
    decision: Literal["accept", "reject", "review"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=400)


class ReviewResolutionNode:
    """Spend a stronger-model call only on a primary LLM's explicit REVIEW."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        decision_brief: str | None = None,
        accept_confidence: float = 0.70,
        max_calls: int = 40,
        timeout_seconds: float = 25.0,
    ) -> None:
        self._llm = llm
        self._brief = decision_brief or ""
        self._accept_confidence = accept_confidence
        self._max_calls = max_calls
        self._timeout_seconds = timeout_seconds
        self._calls = 0

    def configure_graph_params(self, params: dict[str, object]) -> None:
        self._accept_confidence = float_param(params, "accept_confidence", self._accept_confidence)
        self._max_calls = int_param(params, "max_calls", self._max_calls)

    async def process(self, item: JobRecord) -> JobRecord:
        metadata = dict(item.metadata)
        primary_raw = metadata.get("_llm_relevance")
        primary = primary_raw if isinstance(primary_raw, dict) else {}
        if (
            item.routing_decision is not MatchDecision.REVIEW
            or str(primary.get("decision")) != "review"
            or self._calls >= self._max_calls
        ):
            return item
        self._calls += 1
        prompt = self._prompt(item, primary)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._llm.extract(prompt, ReviewResolution)
        except Exception as exc:
            trace = {"called": True, "outcome": "failed", "error": type(exc).__name__}
            return item.model_copy(update={"metadata": {**metadata, "review_resolution": trace}})

        if result.decision == "accept" and result.confidence >= self._accept_confidence:
            decision = MatchDecision.ACCEPT
        elif result.decision == "reject":
            decision = MatchDecision.REJECT
        else:
            decision = MatchDecision.REVIEW
        trace = {
            "called": True,
            "outcome": result.decision,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "final_decision": decision.value,
        }
        reasons = tuple(
            dict.fromkeys((*item.review_reasons, f"review_resolution:{result.decision}"))
        )
        return item.model_copy(
            update={
                "routing_decision": decision,
                "review_reasons": reasons,
                "metadata": {**metadata, "review_resolution": trace},
            }
        )

    def _prompt(self, item: JobRecord, primary: dict[str, object]) -> str:
        fields = {
            "title": item.title,
            "responsibilities": item.responsibilities,
            "must_have_skills": item.must_have_skills,
            "role_family": item.role_family,
            "role_track": item.role_track,
            "seniority": item.seniority.value,
            "primary_reason": primary.get("reasoning"),
            "description": (item.description or "")[:4000],
        }
        return (
            "Make a final relevance decision for this vacancy. Focus on stated responsibilities, "
            "not title or generic AI/ML mentions. Accept only when the role clearly matches; reject "
            "when it clearly does not; otherwise review. Return the requested JSON only.\n"
            f"\nPROFILE BRIEF:\n{self._brief[:2200]}\n\nVACANCY:\n{fields}"
        )
