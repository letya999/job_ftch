"""Cheap early-signal triage for sanitized raw items."""

from __future__ import annotations

import re

from application.drops import RawItemDropped
from domain import RawItem, SourceKind, TriageRejectionReason

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#]+")
_JOB_SIGNAL_PATTERNS = (
    "hiring",
    "vacancy",
    "opening",
    "position",
    "role",
    "engineer",
    "developer",
    "manager",
    "product",
    "project manager",
    "scientist",
    "analyst",
    "architect",
    "designer",
    "specialist",
    "mlops",
    "genai",
    "llm",
    "ai ",
    " ai",
    "remote",
    "onsite",
    "hybrid",
    "salary",
    "full-time",
    "part-time",
    "contract",
    "ваканс",
    "ищем",
    "требован",
    "обязанност",
    "менеджер",
    "инженер",
    "разработ",
    "удален",
)
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
_IRRELEVANT_PATTERNS = (
    "subscribe",
    "follow us",
    "webinar",
    "meetup",
    "conference",
    "course",
    "training",
    "newsletter",
    "digest",
    "news",
    "podcast",
    "like and share",
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


def _has_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


class HeuristicTriageNode:
    def __init__(self, *, min_text_tokens: int = 3, min_text_chars: int = 18) -> None:
        self._min_text_tokens = min_text_tokens
        self._min_text_chars = min_text_chars

    async def process(self, item: RawItem) -> RawItem | None:
        text = item.text.strip()
        lowered = text.casefold()
        if _token_count(text) < self._min_text_tokens or len(text) < self._min_text_chars:
            raise RawItemDropped(
                reason=TriageRejectionReason.TOO_SHORT,
                details="Sanitized text is too short for extraction.",
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
        if _has_pattern(lowered, _COMMENT_SIGNAL_PATTERNS) or _has_pattern(
            lowered, _JOB_SIGNAL_PATTERNS
        ):
            return
        raise RawItemDropped(
            reason=TriageRejectionReason.TELEGRAM_LOW_SIGNAL,
            details="Telegram comment does not contain vacancy follow-up signals.",
            item=item,
        )

    def _ensure_telegram_post_has_signal(self, item: RawItem, lowered: str) -> None:
        if _has_pattern(lowered, _JOB_SIGNAL_PATTERNS):
            return
        reason = (
            TriageRejectionReason.IRRELEVANT_CONTENT
            if _has_pattern(lowered, _IRRELEVANT_PATTERNS)
            else TriageRejectionReason.TELEGRAM_LOW_SIGNAL
        )
        raise RawItemDropped(
            reason=reason,
            details="Telegram post does not contain enough vacancy signal.",
            item=item,
        )

    def _ensure_career_page_has_job_signal(self, item: RawItem, lowered: str) -> None:
        metadata_values = " ".join(str(value).casefold() for value in item.metadata.values())
        if _has_pattern(lowered, _JOB_SIGNAL_PATTERNS):
            return
        if _has_pattern(lowered, _CAREER_NAVIGATION_PATTERNS) or _has_pattern(
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
