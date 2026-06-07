"""AI-role relevance filtering."""

from __future__ import annotations

from application.drops import RawItemDropped
from domain import FilterProfile, Job, JobValidationRejectionReason


class AIRoleRelevanceNode:
    def __init__(self, *, profile: FilterProfile | None = None) -> None:
        self._profile = profile if profile is not None else FilterProfile.default()

    async def process(self, item: Job) -> Job | None:
        haystack = " ".join(
            part for part in (item.title or "", item.company or "", item.description) if part
        ).casefold()

        # Negative check
        if any(keyword in haystack for keyword in self._profile.negative_relevance_keywords):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job matches an explicit non-target role pattern.",
                item=item,
                stage=self.__class__.__name__,
            )

        # Skip positive check if no keywords defined (passthrough)
        if not self._profile.positive_relevance_keywords:
            return item

        # Positive scoring
        positive_hits = sum(
            1 for keyword in self._profile.positive_relevance_keywords if keyword in haystack
        )
        relevance_score = min(1.0, positive_hits / 3.0)

        if relevance_score <= self._profile.relevance_threshold:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job does not match the target AI-jobs niche (score too low).",
                item=item,
                stage=self.__class__.__name__,
            )

        return item.model_copy(update={"relevance_score": relevance_score})
