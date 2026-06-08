from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.domain import JobExtractionStatus, RawItem, SourceKind, WorkMode
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.nodes.extraction import ExtractionNode


class ExplodingLLMProvider:
    async def extract(self, text: str, schema: type[object]) -> object:
        del text, schema
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_extraction_node_emits_partial_job_when_llm_fails() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="ClickHouse",
        external_id="1",
        url="https://job-boards.greenhouse.io/clickhouse/jobs/1",
        text="Senior AI Product Engineer\nRemote Europe\nBuild agent tooling",
        metadata={"job_url": "https://job-boards.greenhouse.io/clickhouse/jobs/1"},
    )

    job = await ExtractionNode(ExplodingLLMProvider()).process(item)

    assert job is not None
    assert job.extraction_status is JobExtractionStatus.PARTIAL
    assert job.company == "ClickHouse"
    assert "partial_extraction" in job.review_reasons


@pytest.mark.asyncio
async def test_heuristic_llm_provider_extracts_work_mode_and_title() -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="2",
        text="LLM Platform Engineer\nBerlin or Remote\nSalary USD 120000 - 160000",
    )

    job = await ExtractionNode(HeuristicLLMProvider()).process(item)

    assert job is not None
    assert job.title == "LLM Platform Engineer"
    assert job.work_mode is WorkMode.REMOTE
    assert job.compensation is not None
    assert job.compensation.max_amount == 160000


@pytest.mark.asyncio
async def test_gold_samples_regression_fixture() -> None:
    fixture_path = Path("fixtures/extraction/gold_samples.jsonl")
    extractor = ExtractionNode(HeuristicLLMProvider())

    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        raw_item = RawItem.model_validate(record["raw_item"])
        job = await extractor.process(raw_item)

        assert job is not None
        assert job.title == record["expected"]["title"]
        assert job.work_mode.value == record["expected"]["work_mode"]
