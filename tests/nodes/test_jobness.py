from __future__ import annotations

import pytest

from job_ftch.domain import JobRecord, RawItem, SourceKind
from job_ftch.nodes.jobness import JobnessDecisionNode, JobnessEvidenceProducer


@pytest.mark.asyncio
async def test_jobness_is_independent_from_completeness_and_preserves_evidence() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="career",
        external_id="1",
        text="Company careers listing",
        metadata={
            "fastpath_completeness": 1.0,
            "post_type_distribution": {"announcement": 0.8, "job_posting": 0.1},
            "structured_source_evidence": [
                {
                    "field_name": "title",
                    "value": "Careers",
                    "evidence_span": "Careers",
                    "page_kind": "listing",
                    "parser_version": "v1",
                    "provenance": "parser",
                    "confidence": 0.9,
                }
            ],
        },
    )

    result = await JobnessDecisionNode().process(item)
    decision = result.metadata["jobness_diagnostic"]

    assert decision["job_probability"] == 0.1
    assert decision["hiring_intent"] is None
    assert decision["evidence"][0]["page_kind"] == "listing"


@pytest.mark.asyncio
async def test_post_extraction_jobness_keeps_job_record_contract() -> None:
    record = JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="source",
        title="AI engineer",
        metadata={"post_type_distribution": {"candidate_seeking": 0.9, "job_posting": 0.1}},
    )

    result = await JobnessEvidenceProducer().process(record)

    assert isinstance(result, JobRecord)
    atom = result.metadata["evidence_atoms"][-1]
    assert atom["claim"] == "is_job"
    assert atom["polarity"] == "contradicts"
    assert atom["strength"] == 0.9


@pytest.mark.asyncio
async def test_raw_jobness_emits_negative_atom_when_job_probability_is_absent() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="source",
        external_id="candidate-post",
        text="Looking for work",
        metadata={"post_type_distribution": {"candidate_seeking": 0.95}},
    )

    result = await JobnessDecisionNode().process(item)

    atom = result.metadata["evidence_atoms"][-1]
    assert atom["polarity"] == "contradicts"
    assert atom["strength"] == 0.95
