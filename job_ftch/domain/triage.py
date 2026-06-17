"""Domain enums for low-cost early triage decisions."""

from __future__ import annotations

from enum import StrEnum


class TriageRejectionReason(StrEnum):
    TOO_SHORT = "too_short"
    IRRELEVANT_CONTENT = "irrelevant_content"
    TELEGRAM_LOW_SIGNAL = "telegram_low_signal"
    LOW_RELEVANCE_PREFILTER = "low_relevance_prefilter"
    CAREER_SITE_NON_JOB_PAGE = "career_site_non_job_page"
    ALREADY_SEEN = "already_seen"
