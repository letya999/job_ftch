"""Domain enums for low-cost early triage decisions."""

from __future__ import annotations

import re
from enum import StrEnum
from html import unescape

from job_ftch.domain.models import SourceKind


class TriageRejectionReason(StrEnum):
    TOO_SHORT = "too_short"
    IRRELEVANT_CONTENT = "irrelevant_content"
    TELEGRAM_LOW_SIGNAL = "telegram_low_signal"
    LOW_RELEVANCE_PREFILTER = "low_relevance_prefilter"
    CAREER_SITE_NON_JOB_PAGE = "career_site_non_job_page"
    VACANCY_CLOSED = "vacancy_closed"
    CANDIDATE_SELF_PROMOTION = "candidate_self_promotion"
    ALREADY_SEEN = "already_seen"


def explicit_non_vacancy(source_kind: str, original: str) -> TriageRejectionReason | None:
    """Recognize observed source notices even on the structured/heuristic fast path."""
    text = original.casefold()
    if source_kind == SourceKind.CAREER_SITE:
        if "this job is no longer open" in text:
            notice = unescape(text).replace("’", "'")
            if "applications aren't accepted any more" in notice:
                return TriageRejectionReason.VACANCY_CLOSED
        # Category title followed by the same title prefixed with a result count.
        lines = [line.strip() for line in text.splitlines()[:4]]
        if (
            len(lines) >= 4
            and lines[0].endswith(" jobs")
            and re.fullmatch(r"[0-9][0-9,]*\s+" + re.escape(lines[0]), lines[1])
        ):
            return TriageRejectionReason.CAREER_SITE_NON_JOB_PAGE
        # An ATS title can be prepended to a job-search page. Check only
        # the beginning of the body so a navigation footer cannot trigger this.
        if len(lines) >= 2 and lines[1].startswith("log in sign up"):
            head = lines[1][:400]
            if "jobs you've hidden" in head and re.search(
                r"\bsearch [\d,]+ work from home [\w -]{1,80} positions\b", head
            ):
                return TriageRejectionReason.CAREER_SITE_NON_JOB_PAGE
    elif source_kind in {SourceKind.TELEGRAM_CHANNEL, SourceKind.TELEGRAM_GROUP}:
        offers_services = re.search(r"\b(?:разрабатываю|создаю|помогаю)\b", text[:500])
        if offers_services and (
            re.search(r"(?<!\w)#ищузаказы\b", text[:250])
            or re.search(r"(?m)^открыта? к проектной работе\b", text)
        ):
            return TriageRejectionReason.CANDIDATE_SELF_PROMOTION
    return None
