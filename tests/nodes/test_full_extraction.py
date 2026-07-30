from __future__ import annotations

from typing import Any

import pytest

from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.domain import (
    EmploymentType,
    JobExtractionStatus,
    LanguageCode,
    MatchDecision,
    Seniority,
)
from job_ftch.nodes.extraction import CoreExtractedJobFields, ExtractedJobFields, ExtractionNode
from job_ftch.nodes.full_extraction import FullExtractionNode


class _RecordingLLM:
    def __init__(self) -> None:
        self.schemas: list[type[Any]] = []

    async def extract(self, _text: str, schema: type[Any]) -> Any:
        self.schemas.append(schema)
        if schema is CoreExtractedJobFields:
            return schema(title="Backend Engineer", company="Acme", description="Python role")
        return schema(
            title="Backend Engineer",
            company="Acme",
            description="Python role",
            responsibilities=["Build APIs"],
            benefits=["Health insurance"],
        )


@pytest.mark.anyio
async def test_core_extraction_uses_small_schema(make_raw_item) -> None:
    llm = _RecordingLLM()
    result = await ExtractionNode(llm, scope="core").process(make_raw_item(text="Python role"))

    assert llm.schemas == [CoreExtractedJobFields]
    assert result is not None
    assert result.responsibilities == ()


@pytest.mark.anyio
async def test_full_extraction_only_calls_llm_for_post_policy_records(make_job_record) -> None:
    llm = _RecordingLLM()
    node = FullExtractionNode(llm)
    rejected = make_job_record(routing_decision=MatchDecision.REJECT)
    accepted = make_job_record(routing_decision=MatchDecision.ACCEPT)

    assert await node.process(rejected) is rejected
    enriched = await node.process(accepted)

    assert llm.schemas == [ExtractedJobFields]
    assert enriched.responsibilities == ("Build APIs",)
    assert enriched.benefits == ("Health insurance",)
    assert enriched.metadata["full_extraction_backend"] == "_RecordingLLM"


@pytest.mark.anyio
async def test_full_extraction_budget_defers_without_calling_llm(make_job_record) -> None:
    llm = _RecordingLLM()
    result = await FullExtractionNode(llm, budget=AsyncCallBudget(0)).process(
        make_job_record(routing_decision=MatchDecision.REVIEW)
    )

    assert llm.schemas == []
    assert result.metadata["full_extraction_outcome"] == "deferred"


class _FieldLLM:
    """Returns whatever field payload the test needs from the full schema."""

    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    async def extract(self, _text: str, schema: type[Any]) -> Any:
        base = {"title": "Backend Engineer", "company": "Acme", "description": "Python role"}
        return schema(**{**base, **self._fields})


@pytest.mark.anyio
async def test_full_extraction_writes_back_card_fields(make_job_record) -> None:
    """The Telegram card renders company/location/work_mode straight off the record."""
    llm = _FieldLLM(
        company="Acme GmbH",
        location="Berlin, DE",
        language=LanguageCode.EN,
        seniority=Seniority.SENIOR,
        employment_type=EmploymentType.FULL_TIME,
        role_family="engineering",
    )
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        company=None,
        location=None,
        language=LanguageCode.UNKNOWN,
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.company == "Acme GmbH"
    assert enriched.location == "Berlin, DE"
    assert enriched.language is LanguageCode.EN
    assert enriched.seniority is Seniority.SENIOR
    assert enriched.employment_type is EmploymentType.FULL_TIME
    assert enriched.role_family == "engineering"


@pytest.mark.anyio
async def test_full_extraction_recovers_location_and_language_from_metadata(
    make_job_record,
) -> None:
    """Acquisition already parsed these; nothing projected them onto the record."""
    llm = _FieldLLM(location=None, language=LanguageCode.UNKNOWN)
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        location=None,
        language=LanguageCode.UNKNOWN,
        metadata={"locations": ["Москва", "Санкт-Петербург"], "detected_language": "ru"},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.location == "Москва"
    assert enriched.language is LanguageCode.RU


@pytest.mark.anyio
async def test_full_extraction_clears_stale_triage_review_reasons(make_job_record) -> None:
    """Triage stamps partial_extraction/missing_* before this node fills the fields."""
    llm = _FieldLLM(company="Acme", location="Berlin")
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        company=None,
        location=None,
        review_reasons=("partial_extraction", "missing_location", "low_quality_score"),
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.extraction_status is JobExtractionStatus.COMPLETE
    assert "partial_extraction" not in enriched.review_reasons
    assert "missing_location" not in enriched.review_reasons
    # Reasons owned by other nodes must survive.
    assert "low_quality_score" in enriched.review_reasons


@pytest.mark.anyio
async def test_full_extraction_keeps_partial_when_company_missing(make_job_record) -> None:
    llm = _FieldLLM(company=None, location="Berlin")
    job = make_job_record(routing_decision=MatchDecision.ACCEPT, company=None)

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.extraction_status is JobExtractionStatus.PARTIAL
    assert enriched.review_reasons[0] == "partial_extraction"
    assert "missing_company" in enriched.review_reasons
    assert "missing_location" not in enriched.review_reasons
