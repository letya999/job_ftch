"""Attach structured source evidence without deciding jobness (ADR-056)."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    PageKind,
    StructuredSourceEvidence,
    source_identity_for_raw_item,
)

if TYPE_CHECKING:
    from job_ftch.domain.models import RawItem

logger = structlog.get_logger("job_ftch.nodes.completeness_gate")

TRUSTED_MONITOR_TYPES = frozenset(
    {
        "greenhouse",
        "lever",
        "ashby",
        "workday",
        "smartrecruiters",
        "workable",
        "personio",
        "recruitee",
        "breezy",
        "deel",
        "rippling",
        "hh",
        "superjob",
    }
)

TRUSTED_EXTRACTION_SOURCES = frozenset(
    {
        "json_ld",
        "structured_api",
    }
)


def score_completeness(meta: dict[str, Any], text: str) -> float:
    """Compute a 0-1 completeness score from metadata fields."""
    score = 0.0
    if meta.get("title"):
        score += 0.25
    if text and len(text.strip()) >= 100:
        score += 0.30
    if meta.get("company") or meta.get("company_name"):
        score += 0.15
    if meta.get("canonical_url") or meta.get("job_url"):
        score += 0.10
    if meta.get("location") or meta.get("locations") or meta.get("city"):
        score += 0.10
    if meta.get("salary") or meta.get("base_salary"):
        score += 0.10
    return min(score, 1.0)


def _classify_trust(item: RawItem, threshold: float) -> tuple[str | None, float]:
    """Return (tier, effective_threshold) or (None, _) if gate should not fire."""
    meta = item.metadata
    monitor = meta.get("monitor_type", "")
    extraction_source = meta.get("extraction_source", "")

    if monitor in TRUSTED_MONITOR_TYPES or extraction_source in TRUSTED_EXTRACTION_SOURCES:
        return "trusted", min(threshold, 0.6)

    if meta.get("extraction_source") == "telegram_structured":
        return "structured", threshold

    completeness = score_completeness(meta, item.text)
    if completeness >= threshold:
        return "structured", threshold

    return None, 1.0


def _page_kind(item: RawItem) -> PageKind:
    explicit = str(item.metadata.get("page_kind", "")).casefold()
    if explicit in {PageKind.DETAIL.value, PageKind.LISTING.value}:
        return PageKind(explicit)
    url = str(item.url or "").casefold().rstrip("/")
    if url.endswith(("/jobs", "/vacancies", "-jobs")):
        return PageKind.LISTING
    return PageKind.UNKNOWN


def _evidence_from_metadata(item: RawItem) -> tuple[StructuredSourceEvidence, ...]:
    metadata = item.metadata
    parser_version = str(metadata.get("parser_version") or "unknown")
    source = str(metadata.get("monitor_type") or metadata.get("extraction_source") or "source")
    evidence: list[StructuredSourceEvidence] = []
    for field_name, keys in {
        "title": ("title",),
        "company": ("company", "company_name"),
        "location": ("location", "city"),
        "canonical_url": ("canonical_url", "job_url"),
        "salary": ("salary", "base_salary"),
    }.items():
        value = next((metadata[key] for key in keys if metadata.get(key)), None)
        if value is not None:
            text = str(value).strip()
            if text:
                evidence.append(
                    StructuredSourceEvidence(
                        field_name=field_name,
                        value=text,
                        evidence_span=text,
                        page_kind=_page_kind(item),
                        parser_version=parser_version,
                        provenance=EvidenceProvenance.PARSER,
                        confidence=0.9 if source in TRUSTED_MONITOR_TYPES else 0.7,
                    )
                )
    return tuple(evidence)


def _atom_from_structured(item: RawItem, evidence: StructuredSourceEvidence) -> EvidenceAtom:
    identity = source_identity_for_raw_item(item)
    digest = sha256(f"{evidence.field_name}|{evidence.value}".encode()).hexdigest()[:16]
    return EvidenceAtom(
        evidence_id=f"{item.stable_id}:field:{digest}",
        claim=ClaimKind.FIELD_VALID,
        subject=evidence.field_name,
        polarity=EvidencePolarity.SUPPORTS,
        strength=evidence.confidence,
        reliability=evidence.confidence,
        provenance=evidence.provenance,
        producer="completeness_gate",
        producer_version=evidence.parser_version,
        source_family=identity.family,
        observation_kind=identity.observation_kind,
        transport=identity.transport,
        independence_key=f"{item.stable_id}:field:{evidence.field_name}",
        observation_id=item.stable_id,
        candidate_id=str(item.metadata.get("candidate_span_id") or item.stable_id),
        evidence_ref=f"metadata:{evidence.field_name}",
    )


class CompletenessGateNode:
    def __init__(self, *, threshold: float = 0.8) -> None:
        self._threshold = threshold

    async def process(self, item: RawItem) -> RawItem:
        tier, effective_threshold = _classify_trust(item, self._threshold)
        completeness = score_completeness(item.metadata, item.text)
        evidence = _evidence_from_metadata(item)
        meta = dict(item.metadata)
        meta["structured_source_evidence"] = [entry.model_dump(mode="json") for entry in evidence]
        meta["evidence_atoms"] = [
            _atom_from_structured(item, entry).model_dump(mode="json") for entry in evidence
        ]
        meta["structured_page_kind"] = _page_kind(item).value
        meta["extraction_cost_hint"] = (
            "structured" if tier is not None and completeness >= effective_threshold else "full"
        )
        meta["fastpath_completeness"] = completeness
        # Completeness is only an extraction-cost signal. In particular it
        # must never assert hiring intent or turn a listing into a vacancy.
        return item.model_copy(update={"metadata": meta})
