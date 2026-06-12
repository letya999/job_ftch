from __future__ import annotations

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import Job, JobDraft, JobRecord, SourceKind, WorkMode
from job_ftch.nodes import (
    AIRoleRelevanceNode,
    CompensationParsingNode,
    JobValidationNode,
    LocationWorkModeNormalizationNode,
    QualityScoringNode,
    TitleCompanyNormalizationNode,
)


def _job(**overrides: object) -> Job:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title": "LLM Platform Engineer",
        "company": "Example Corp",
        "description": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.UNKNOWN,
    }
    payload.update(overrides)
    return Job.model_validate(payload)


def _draft(**overrides: object) -> JobDraft:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title_raw": "LLM Platform Engineer",
        "company_name_raw": "Example Corp",
        "description_raw": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.UNKNOWN,
    }
    payload.update(overrides)
    return JobDraft.model_validate(payload)


def _record(**overrides: object) -> JobRecord:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title": "LLM Platform Engineer",
        "company": "Example Corp",
        "description": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.UNKNOWN,
    }
    payload.update(overrides)
    return JobRecord.model_validate(payload)


@pytest.mark.asyncio
async def test_title_company_normalization_splits_company_from_title() -> None:
    node = TitleCompanyNormalizationNode()

    job = await node.process(_draft(title_raw="Hiring: AI Infra Engineer at Example AI", company_name_raw=None))

    assert job is not None
    assert isinstance(job, JobRecord)
    assert job.title == "AI Infra Engineer"
    assert job.company == "Example AI"


@pytest.mark.asyncio
async def test_location_work_mode_normalization_detects_remote() -> None:
    node = LocationWorkModeNormalizationNode()

    job = await node.process(_record(location="Remote", description="Remote AI PM role"))

    assert job is not None
    assert job.location is None
    assert job.work_mode is WorkMode.REMOTE


@pytest.mark.asyncio
async def test_compensation_parsing_node_reads_salary_from_description() -> None:
    node = CompensationParsingNode()

    job = await node.process(
        _record(description="LLM engineer role. Compensation USD 120000 - 160000 plus bonus.")
    )

    assert job is not None
    assert job.compensation is not None
    assert job.compensation.min_amount == 120000


@pytest.mark.asyncio
async def test_ai_role_relevance_drops_out_of_scope_jobs() -> None:
    node = AIRoleRelevanceNode()

    with pytest.raises(RawItemDropped, match="non-target role pattern"):
        await node.process(
            _job(
                title="Office Manager",
                description="Coordinate office operations and travel.",
            )
        )


@pytest.mark.asyncio
async def test_quality_scoring_and_validation_keep_good_job() -> None:
    scorer = QualityScoringNode()
    validator = JobValidationNode()
    relevant = _record(
        description=(
            "Build AI infra, LLM evaluation, prompt tooling, and agent platform "
            "services for enterprise customers."
        ),
        canonical_url="https://example.com/jobs/1",
        location="Berlin",
        relevance_score=0.9,
    )
    scored = await scorer.process(relevant)
    assert scored is not None

    validated = await validator.process(scored)

    assert validated is not None
    assert validated.quality_score is not None
    assert validated.quality_score >= 0.25
