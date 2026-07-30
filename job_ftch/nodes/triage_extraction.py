"""Core-only extraction for the H13 relevance-card experiment."""

from __future__ import annotations

from job_ftch.domain import JobDraft, RawItem, RelevanceCard
from job_ftch.nodes.extraction import ExtractionNode


class TriageExtractionNode:
    """Create only the fields needed to judge relevance.

    The output remains a ``JobDraft`` so the existing normalization seam can be
    reused. Full field enrichment is deliberately delegated to
    ``FullExtractionNode`` after terminal routing.
    """

    def __init__(self, llm: object, *, target_roles: tuple[str, ...] = ()) -> None:
        self._extractor = ExtractionNode(llm, target_roles=target_roles, scope="core")  # type: ignore[arg-type]

    async def process(self, item: RawItem) -> JobDraft | None:
        draft = await self._extractor.process(item)
        if draft is None:
            return None
        card = RelevanceCard(
            title=draft.title_raw,
            employer=draft.company_name_raw,
            seniority=str(draft.seniority),
            role_anchors=tuple(value for value in (draft.role_family, draft.role_track) if value),
            location=draft.location_raw,
            salary_present=draft.compensation is not None,
            text=draft.description_raw[:2000],
            evidence_spans=tuple(draft.responsibilities[:3]),
        )
        return draft.model_copy(
            update={"metadata": {**draft.metadata, "relevance_card": card.model_dump(mode="json")}}
        )
