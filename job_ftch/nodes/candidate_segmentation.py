"""Explicit RawItem -> CandidateSpan[] segmentation boundary (ADR-055)."""

from __future__ import annotations

import re

from job_ftch.domain import (
    CandidateSpan,
    ObservationKind,
    RawItem,
    source_identity_for_raw_item,
)

_DIGEST_BREAK = re.compile(r"\n\s*(?=(?:\d+[.)]|[-*•])\s+)")
_VACANCY_HINT = re.compile(r"\b(?:vacanc(?:y|ies)|hiring|ищем|ваканси[яи])\b", re.IGNORECASE)


class CandidateSegmentationNode:
    """Split explicit source arrays and text digests without cross-span state."""

    is_fan_out_stage = True

    async def process(self, item: RawItem) -> tuple[CandidateSpan, ...]:
        identity = source_identity_for_raw_item(item)
        is_confirmed_detail = (
            identity.observation_kind is ObservationKind.VACANCY_DETAIL
            and item.metadata.get("detail_vacancy_confirmed") is True
        )
        # A detail page is one vacancy even when its responsibilities and
        # requirements are formatted as a numbered or bulleted list.
        segments = (
            [(item.text, "confirmed_vacancy_detail")]
            if is_confirmed_detail
            else self._source_segments(item) or self._text_segments(item.text)
        )
        return tuple(
            CandidateSpan(
                parent_observation_id=item.stable_id,
                ordinal=ordinal,
                text=text,
                raw_item=item,
                source_evidence=(evidence,),
                context_evidence=self._context_evidence(item),
            )
            for ordinal, (text, evidence) in enumerate(segments)
        )

    @staticmethod
    def _source_segments(item: RawItem) -> list[tuple[str, str]]:
        raw_segments = item.metadata.get("candidate_segments")
        if not isinstance(raw_segments, (list, tuple)):
            return []
        result: list[tuple[str, str]] = []
        for raw in raw_segments:
            text = raw.get("text") if isinstance(raw, dict) else raw
            if isinstance(text, str) and text.strip():
                result.append((text.strip(), "source_declared_segment"))
        return result

    @staticmethod
    def _text_segments(text: str) -> list[tuple[str, str]]:
        parts = [part.strip() for part in _DIGEST_BREAK.split(text) if part.strip()]
        if len(parts) > 1 and sum(bool(_VACANCY_HINT.search(part)) for part in parts) >= 2:
            return [(part, "digest_boundary") for part in parts]
        return [(text, "whole_observation")]

    @staticmethod
    def _context_evidence(item: RawItem) -> tuple[str, ...]:
        evidence: list[str] = []
        for key in ("parent_text", "reply_chain_text", "linked_message_text"):
            value = item.metadata.get(key)
            if isinstance(value, str) and value.strip():
                evidence.append(f"{key}:{value.strip()}")
        return tuple(evidence)
