"""Language/context enrichment for raw items before semantic triage."""

from __future__ import annotations

import re

from job_ftch.domain import (
    LanguageCode,
    RawItem,
    SourceFamily,
    SourceKind,
    source_identity_for_raw_item,
)
from job_ftch.domain.posting_language import posting_language

_HASHTAG_RE = re.compile(r"#\w+")
_URL_RE = re.compile(r"https?://\S+")


class SourceContextNode:
    """
    Stage[RawItem, RawItem] — Enriches RawItem with language, family, trust, and parsing hints.
    """

    async def process(self, item: RawItem) -> RawItem | None:
        # 1. Language detection
        declared = item.metadata.get("detected_language")
        language = (
            LanguageCode(declared)
            if isinstance(declared, str)
            and declared in {code.value for code in LanguageCode if code != LanguageCode.UNKNOWN}
            else posting_language(item.text)
        )

        # 2. Source family and trust scoring
        identity = source_identity_for_raw_item(item)
        family = identity.family
        trust = 0.5

        sk = item.source_kind
        if sk == SourceKind.CAREER_SITE:
            family = SourceFamily.CAREER_WEB
            trust = 0.9
        elif sk == SourceKind.TELEGRAM_CHANNEL:
            family = SourceFamily.TELEGRAM
            trust = 0.7
        elif sk == SourceKind.TELEGRAM_GROUP:
            family = SourceFamily.TELEGRAM
            trust = 0.5
        elif sk == SourceKind.TELEGRAM_COMMENT:
            family = SourceFamily.TELEGRAM
            trust = 0.3
        elif sk == SourceKind.DEBUG:
            family = SourceFamily.FIXTURE
            trust = 1.0
        identity = identity.model_copy(update={"family": family})

        # 3. Parsing hints for Telegram
        hints: dict[str, object] = {}
        if family is SourceFamily.TELEGRAM:
            hints["has_hashtags"] = bool(_HASHTAG_RE.search(item.text))
            hints["has_urls"] = bool(_URL_RE.search(item.text))
            hints["approx_word_count"] = len(item.text.split())

        metadata = {
            **item.metadata,
            "detected_language": language.value,
            "source_family": family.value,
            "observation_kind": identity.observation_kind.value,
            "transport": identity.transport.value,
            "source_trust": trust,
            "source_context": f"{item.source_kind.value}:{item.source_name.casefold()}",
            **hints,
        }

        return item.model_copy(update={"metadata": metadata, "source_identity": identity})
