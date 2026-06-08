"""Cheap early-signal triage for sanitized raw items."""

from __future__ import annotations

import re
from functools import lru_cache

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import FilterProfile, RawItem, SourceKind, TriageRejectionReason

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#]+")

_COMMENT_SIGNAL_PATTERNS = (
    "candidate",
    "candidates",
    "portfolio",
    "resume",
    "cv",
    "dm",
    "timezone",
    "utc",
    "salary",
    "compensation",
    "relocation",
    "remote",
    "onsite",
    "hybrid",
    "stack",
)

_CAREER_NAVIGATION_PATTERNS = (
    "about us",
    "our team",
    "our culture",
    "company values",
    "our mission",
    "benefits",
    "why join us",
    "contacts",
    "faq",
    "newsroom",
    "blog",
    "culture",
    "компания",
    "о нас",
    "ценности",
    "контакты",
    "блог",
)


def _token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


@lru_cache(maxsize=256)
def _compile_keyword(kw: str) -> re.Pattern[str]:
    # Use word boundary for keywords <=5 chars with no spaces (e.g. "ai", "ml", "rag")
    # Use substring match for multi-word phrases (e.g. "machine learning")
    stripped = kw.strip()
    if len(stripped) <= 5 and " " not in stripped:
        return re.compile(rf"\b{re.escape(stripped)}\b", re.IGNORECASE)
    return re.compile(re.escape(stripped), re.IGNORECASE)


def _has_any(text: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(_compile_keyword(p).search(text) for p in patterns)


class HeuristicTriageNode:
    def __init__(self, *, profile: FilterProfile | None = None) -> None:
        self._profile = profile if profile is not None else FilterProfile.default()

    async def process(self, item: RawItem) -> RawItem | None:
        text = item.text.strip()
        lowered = text.casefold()

        # Source kind filter
        if (
            self._profile.allowed_source_kinds is not None
            and item.source_kind not in self._profile.allowed_source_kinds
        ):
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details=f"Source kind '{item.source_kind}' is not allowed by profile.",
                item=item,
            )

        # Length check
        if (
            _token_count(text) < self._profile.min_text_tokens
            or len(text) < self._profile.min_text_chars
        ):
            raise RawItemDropped(
                reason=TriageRejectionReason.TOO_SHORT,
                details="Sanitized text is too short for extraction.",
                item=item,
            )

        # Required keywords check
        if self._profile.required_keywords and not all(
            keyword.casefold() in lowered for keyword in self._profile.required_keywords
        ):
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details="Required keywords not found in text.",
                item=item,
            )

        if item.source_kind is SourceKind.CAREER_SITE:
            self._ensure_career_page_has_job_signal(item, lowered)
            return item
        if item.source_kind is SourceKind.TELEGRAM_COMMENT:
            self._ensure_telegram_comment_has_signal(item, lowered)
            return item
        if item.source_kind in {SourceKind.TELEGRAM_CHANNEL, SourceKind.TELEGRAM_GROUP}:
            self._ensure_telegram_post_has_signal(item, lowered)
        return item

    def _ensure_telegram_comment_has_signal(self, item: RawItem, lowered: str) -> None:
        if _has_any(lowered, _COMMENT_SIGNAL_PATTERNS) or _has_any(
            lowered, self._profile.positive_relevance_keywords
        ):
            return
        raise RawItemDropped(
            reason=TriageRejectionReason.TELEGRAM_LOW_SIGNAL,
            details="Telegram comment does not contain vacancy follow-up signals.",
            item=item,
        )

    def _ensure_telegram_post_has_signal(self, item: RawItem, lowered: str) -> None:
        if _has_any(lowered, self._profile.positive_relevance_keywords):
            return
        reason = (
            TriageRejectionReason.IRRELEVANT_CONTENT
            if _has_any(lowered, self._profile.exclude_keywords)
            else TriageRejectionReason.TELEGRAM_LOW_SIGNAL
        )
        raise RawItemDropped(
            reason=reason,
            details="Telegram post does not contain enough vacancy signal.",
            item=item,
        )

    def _ensure_career_page_has_job_signal(self, item: RawItem, lowered: str) -> None:
        metadata_values = " ".join(str(value).casefold() for value in item.metadata.values())
        if _has_any(lowered, self._profile.positive_relevance_keywords):
            return
        if _has_any(lowered, _CAREER_NAVIGATION_PATTERNS) or _has_any(
            metadata_values, _CAREER_NAVIGATION_PATTERNS
        ):
            raise RawItemDropped(
                reason=TriageRejectionReason.CAREER_SITE_NON_JOB_PAGE,
                details="Career-site page looks like navigation or generic company content.",
                item=item,
            )
        if not any(key in item.metadata for key in ("department", "location", "job_url", "badges")):
            raise RawItemDropped(
                reason=TriageRejectionReason.CAREER_SITE_NON_JOB_PAGE,
                details="Career-site page does not expose stable job-like metadata.",
                item=item,
            )
