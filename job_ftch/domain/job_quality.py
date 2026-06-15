"""Domain enums for extraction and post-extraction job quality decisions."""

from __future__ import annotations

from enum import StrEnum


class ExtractionRejectionReason(StrEnum):
    EXTRACTION_EMPTY = "extraction_empty"
    EXTRACTION_FAILED = "extraction_failed"
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"


class JobReviewReason(StrEnum):
    PARTIAL_EXTRACTION = "partial_extraction"
    MISSING_COMPANY = "missing_company"
    MISSING_LOCATION = "missing_location"
    MISSING_TITLE = "missing_title"
    LOW_QUALITY_SCORE = "low_quality_score"


class JobValidationRejectionReason(StrEnum):
    JOB_DESCRIPTION_TOO_SHORT = "job_description_too_short"
    JOB_MISSING_CORE_FIELDS = "job_missing_core_fields"
    JOB_OUT_OF_SCOPE = "job_out_of_scope"
    JOB_TOO_LOW_QUALITY = "job_too_low_quality"
