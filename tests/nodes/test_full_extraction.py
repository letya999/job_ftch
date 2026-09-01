from __future__ import annotations

from typing import Any

import pytest

from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.domain import (
    CompensationRange,
    EmploymentType,
    JobExtractionStatus,
    LanguageCode,
    MatchDecision,
    Seniority,
    WorkMode,
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
async def test_authoritative_parser_identity_outranks_llm(make_job_record) -> None:
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        company=None,
        country=None,
        metadata={
            "company": "Яндекс",
            "company_authoritative": True,
            "country": "Узбекистан",
            "country_authoritative": True,
        },
    )

    enriched = await FullExtractionNode(_FieldLLM(company="сомнительная компания")).process(job)

    assert enriched.company == "Яндекс"
    assert enriched.country == "Узбекистан"


@pytest.mark.anyio
async def test_detail_country_outranks_regional_default(make_job_record) -> None:
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        country="Казахстан",
        metadata={"country": "Узбекистан", "country_authoritative": True},
    )

    enriched = await FullExtractionNode(_FieldLLM()).process(job)

    assert enriched.country == "Казахстан"


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


@pytest.mark.anyio
async def test_office_description_falls_back_to_metadata(make_job_record) -> None:
    """A Sberbank posting listed "комфортный современный офис рядом с
    м. Кутузовская" among its benefits; the LLM read that as the location while
    metadata, parsed from the site's own API, held "г Москва"."""
    llm = _FieldLLM(location="офис рядом с м. Кутузовская")
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        location=None,
        metadata={"locations": ["г Москва"]},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.location == "г Москва"


@pytest.mark.anyio
async def test_sber_business_effect_is_not_salary(make_job_record) -> None:
    llm = _FieldLLM(compensation=CompensationRange(currency="RUB", min_amount=2))
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        metadata={"parser": "sber-public-api"},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.compensation is None


@pytest.mark.anyio
async def test_bare_country_code_falls_back_to_metadata(make_job_record) -> None:
    """A habr posting answered "RU" - a country code is not a place."""
    llm = _FieldLLM(location="RU")
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        location=None,
        metadata={"locations": ["Москва, Россия"]},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.location == "Москва, Россия"


@pytest.mark.anyio
async def test_usable_extracted_location_is_not_overridden(make_job_record) -> None:
    """Metadata is not reliably better: on hh.ru it carries the search scope,
    and one record would have relocated a German town to Moscow."""
    llm = _FieldLLM(location="Bonnatal, Germany")
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        location=None,
        metadata={"locations": ["Москва, Московская область, RU"]},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.location == "Bonnatal, Germany"


@pytest.mark.anyio
async def test_full_extraction_rebuilds_city_country_from_post_accept_location(
    make_job_record,
) -> None:
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        city=None,
        country="Россия",
        location=None,
    )

    enriched = await FullExtractionNode(_FieldLLM(location="Warsaw, Poland")).process(job)

    assert enriched.city == "Варшава"
    assert enriched.country == "Польша"


@pytest.mark.anyio
async def test_llm_location_used_when_metadata_has_none(make_job_record) -> None:
    """Prose stays the primary source; sites without structured geo still work."""
    llm = _FieldLLM(location="Berlin, DE")
    job = make_job_record(routing_decision=MatchDecision.ACCEPT, location=None, metadata={})

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.location == "Berlin, DE"


@pytest.mark.anyio
async def test_work_mode_recovered_from_schema_org_telecommute(make_job_record) -> None:
    """Several sites publish jobLocationType in JSON-LD and say nothing in prose."""
    llm = _FieldLLM(work_mode=WorkMode.UNKNOWN)
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        work_mode=WorkMode.UNKNOWN,
        metadata={"job_location_type": "TELECOMMUTE"},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.work_mode is WorkMode.REMOTE


@pytest.mark.anyio
async def test_llm_work_mode_outranks_metadata(make_job_record) -> None:
    """Metadata is the recovery path for work mode, not an override."""
    llm = _FieldLLM(work_mode=WorkMode.HYBRID)
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        work_mode=WorkMode.UNKNOWN,
        metadata={"job_location_type": "TELECOMMUTE"},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.work_mode is WorkMode.HYBRID


@pytest.mark.anyio
async def test_tools_stack_falls_back_to_metadata_tags(make_job_record) -> None:
    """Switching hirify to its API removed the on-page keyword list the LLM had
    been reading tools out of, and tools_stack coverage for that source fell to
    zero - while the tags sat unused in metadata."""
    llm = _FieldLLM(tools_stack=())
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        tools_stack=(),
        metadata={"skills": ["python", "fastapi", "python", "docker"]},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.tools_stack == ("python", "fastapi", "docker")


@pytest.mark.anyio
async def test_extracted_tools_stack_wins_over_metadata_tags(make_job_record) -> None:
    """The fallback must stay additive: a real extraction is never replaced."""
    llm = _FieldLLM(tools_stack=("pytorch", "langgraph"))
    job = make_job_record(
        routing_decision=MatchDecision.ACCEPT,
        tools_stack=(),
        metadata={"skills": ["php"]},
    )

    enriched = await FullExtractionNode(llm).process(job)

    assert enriched.tools_stack == ("pytorch", "langgraph")
