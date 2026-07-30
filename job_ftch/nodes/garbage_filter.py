"""Layer A: non-destructive universal garbage evidence (Phase 2.1)."""

from __future__ import annotations

from hashlib import sha256

from opentelemetry import trace

from job_ftch.application.garbage import garbage_reason
from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    RawItem,
    SourceKind,
    source_identity_for_raw_item,
)

_tracer = trace.get_tracer("job_ftch.nodes")

_CAREER_PAGE_TITLE_PATTERNS = (
    "our locations",
    "locations",
    "what we offer",
    "benefits",
    "about us",
    "our culture",
    "company values",
    "вакансии напрямую от компаний",
    "релевантные предложения",
)

_CAREER_PAGE_PATH_SEGMENTS = (
    "/content/",
    "/locations",
    "/careers/locations",
    "/company/",
    "/companies/",
    "/blog/",
)


def _career_site_non_job_reason(item: RawItem) -> str | None:
    if item.source_kind is not SourceKind.CAREER_SITE:
        return None

    url = str(item.url or "").casefold()
    text = (item.text or "").strip()
    head = text[:500].casefold()
    title = text.splitlines()[0].strip().casefold() if text.splitlines() else ""

    if any(pattern in title for pattern in _CAREER_PAGE_TITLE_PATTERNS):
        return "career-site generic page title"

    if any(segment in url for segment in _CAREER_PAGE_PATH_SEGMENTS):
        path = url.split("?", 1)[0].rstrip("/")
        if path.endswith("/vacancies"):
            return "career-site vacancy listing page"
        if "/companies/" in path and path.endswith("/vacancies"):
            return "career-site company vacancy listing page"
        if "/locations" in path or "/content/" in path or "/blog/" in path:
            return "career-site navigation page"

    path = url.split("?", 1)[0].rstrip("/")
    if path.endswith(("-jobs", "/jobs", "/vacancies")):
        return "career-site listing/category page"

    if "api." in url and "/vacancies?" in url and ("per_page=0" in url or "clusters=true" in url):
        return "career-site search/count endpoint"

    if "directly from companies" in head or "напрямую от компаний" in head:
        return "career-site category page"

    return None


class GarbageFilterNode:
    """Attach garbage evidence; policy decides any terminal outcome later."""

    async def process(self, item: RawItem) -> RawItem | None:
        with _tracer.start_as_current_span("garbage_filter.check") as span:
            span.set_attribute("job_ftch.node", "GarbageFilterNode")
            reason = _career_site_non_job_reason(item)
            if reason is None:
                reason = garbage_reason(item.text or "")
            if reason is None:
                span.set_attribute("job_ftch.node.result", "pass")
                return item

            span.set_attribute("job_ftch.garbage_filter.evidence", reason)
            span.set_attribute("job_ftch.node.result", "evidence")
            identity = source_identity_for_raw_item(item)
            digest = sha256(reason.encode()).hexdigest()[:16]
            atom = EvidenceAtom(
                evidence_id=f"{item.stable_id}:garbage:{digest}",
                claim=ClaimKind.IS_JOB,
                subject="vacancy",
                polarity=EvidencePolarity.CONTRADICTS,
                strength=0.9,
                reliability=0.85,
                provenance=EvidenceProvenance.INFERRED,
                producer="garbage_filter",
                producer_version="garbage-v2",
                source_family=identity.family,
                observation_kind=identity.observation_kind,
                transport=identity.transport,
                independence_key=f"{item.stable_id}:garbage",
                observation_id=item.stable_id,
                candidate_id=str(item.metadata.get("candidate_span_id") or item.stable_id),
                evidence_ref=f"raw:garbage:{reason}",
            )
            return item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "garbage_evidence": reason,
                        "early_triage_state": "uncertain",
                        "evidence_atoms": [
                            *item.metadata.get("evidence_atoms", []),
                            atom.model_dump(mode="json"),
                        ],
                    }
                }
            )
