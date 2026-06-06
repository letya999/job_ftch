from __future__ import annotations

import pytest

from application.drops import RawItemDropped
from domain import Job, SourceKind, WorkMode
from nodes import (
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


@pytest.mark.asyncio
async def test_title_company_normalization_splits_company_from_title() -> None:
    node = TitleCompanyNormalizationNode()

    job = await node.process(_job(title="Hiring: AI Infra Engineer at Example AI", company=None))

    assert job is not None
    assert job.title == "AI Infra Engineer"
    assert job.company == "Example AI"


@pytest.mark.asyncio
async def test_location_work_mode_normalization_detects_remote() -> None:
    node = LocationWorkModeNormalizationNode()

    job = await node.process(_job(location="Remote", description="Remote AI PM role"))

    assert job is not None
    assert job.location is None
    assert job.work_mode is WorkMode.REMOTE


@pytest.mark.asyncio
async def test_compensation_parsing_node_reads_salary_from_description() -> None:
    node = CompensationParsingNode()

    job = await node.process(
        _job(description="LLM engineer role. Compensation USD 120000 - 160000 plus bonus.")
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
    relevance = AIRoleRelevanceNode()

    relevant = await relevance.process(
        _job(
            description=(
                "Build AI infra, LLM evaluation, prompt tooling, and agent platform "
                "services for enterprise customers."
            ),
            canonical_url="https://example.com/jobs/1",
            location="Berlin",
        )
    )
    assert relevant is not None
    scored = await scorer.process(relevant)
    assert scored is not None

    validated = await validator.process(scored)

    assert validated is not None
    assert validated.quality_score is not None
    assert validated.quality_score >= 0.25
