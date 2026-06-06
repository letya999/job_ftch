"""AI-role relevance filtering."""

from __future__ import annotations

from application.drops import RawItemDropped
from domain import Job, JobValidationRejectionReason

_POSITIVE_KEYWORDS = (
    "ai",
    "llm",
    "genai",
    "mlops",
    "ml ",
    "machine learning",
    "agent",
    "rag",
    "prompt",
    "infra",
    "platform",
    "data scientist",
    "ai pm",
    "ai product",
)
_NEGATIVE_KEYWORDS = (
    "sales",
    "account executive",
    "hr",
    "recruiter",
    "office manager",
    "marketing",
)


class AIRoleRelevanceNode:
    async def process(self, item: Job) -> Job | None:
        haystack = " ".join(
            part for part in (item.title or "", item.company or "", item.description) if part
        ).casefold()
        if any(keyword in haystack for keyword in _NEGATIVE_KEYWORDS):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job matches an explicit non-target role pattern.",
                item=item,
                stage=self.__class__.__name__,
            )
        positive_hits = sum(1 for keyword in _POSITIVE_KEYWORDS if keyword in haystack)
        relevance_score = min(1.0, positive_hits / 3.0)
        if relevance_score == 0.0:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job does not match the target AI-jobs niche.",
                item=item,
                stage=self.__class__.__name__,
            )
        return item.model_copy(update={"relevance_score": relevance_score})

