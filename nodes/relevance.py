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

        # Negative check (always applies)
        if any(keyword in haystack for keyword in self._profile.negative_relevance_keywords):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details="Job matches an explicit non-target role pattern.",
                item=item,
                stage=self.__class__.__name__,
            )

        # If LLM already computed ai_relevance during extraction, use it directly
        if item.ai_relevance > 0.0:
            if item.ai_relevance <= self._profile.relevance_threshold:
                raise RawItemDropped(
                    reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                    details=f"LLM-estimated ai_relevance={item.ai_relevance:.2f} is below threshold.",
                    item=item,
                    stage=self.__class__.__name__,
                )
            return item  # already has relevance_score set by LLM

        # Also drop candidate/spam post types detected during extraction
        from domain import PostType  # avoid circular at module level, use local import

        if item.post_type in (PostType.CANDIDATE_SEEKING, PostType.SPAM):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details=f"Extracted post_type={item.post_type.value!r} is not a job posting.",
                item=item,
                stage=self.__class__.__name__,
            )

        # Fallback: keyword scoring (when ai_relevance was not set by LLM)
        if not self._profile.positive_relevance_keywords:
            return item
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
