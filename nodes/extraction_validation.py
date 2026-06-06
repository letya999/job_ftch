"""Post-extraction validation for minimum structured quality."""

from __future__ import annotations

from application.drops import RawItemDropped
from domain import Job, JobReviewReason, JobValidationRejectionReason


class ExtractionValidationNode:
    def __init__(self, *, min_description_chars: int = 30) -> None:
        self._min_description_chars = min_description_chars

    async def process(self, item: Job) -> Job | None:
        if len(item.description.strip()) < self._min_description_chars:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_DESCRIPTION_TOO_SHORT,
                details="Extracted job description is too short to be useful.",
                item=item,
                stage=self.__class__.__name__,
            )
        review_reasons = list(item.review_reasons)
        if item.title is None and item.company is None and item.canonical_url is None:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_MISSING_CORE_FIELDS,
                details="Extracted job is missing title, company, and canonical URL.",
                item=item,
                stage=self.__class__.__name__,
            )
        if item.location is None and JobReviewReason.MISSING_LOCATION.value not in review_reasons:
            review_reasons.append(JobReviewReason.MISSING_LOCATION.value)
        return item.model_copy(update={"review_reasons": tuple(review_reasons)})

