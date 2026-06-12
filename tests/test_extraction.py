from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

import pytest

from job_ftch.domain import JobDraft, JobExtractionStatus, RawItem, SourceKind, WorkMode
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.nodes.extraction import ExtractionNode

ExtractedItem = TypeVar("ExtractedItem")


class ExplodingLLMProvider:
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        del text, schema
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_extraction_node_emits_partial_draft_when_llm_fails() -> None:
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.CAREER_SITE,
            "source_name": "ClickHouse",
            "external_id": "1",
            "url": "https://job-boards.greenhouse.io/clickhouse/jobs/1",
            "text": "Senior AI Product Engineer\nRemote Europe\nBuild agent tooling",
            "metadata": {"job_url": "https://job-boards.greenhouse.io/clickhouse/jobs/1"},
        }
    )

    draft = await ExtractionNode(ExplodingLLMProvider()).process(item)

    assert draft is not None
    assert isinstance(draft, JobDraft)
    assert draft.extraction_status is JobExtractionStatus.PARTIAL
    assert draft.company_name_raw == "ClickHouse"
    assert "partial_extraction" in draft.review_reasons


@pytest.mark.asyncio
async def test_heuristic_llm_provider_extracts_work_mode_and_title() -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="2",
        text="LLM Platform Engineer\nBerlin or Remote\nSalary USD 120000 - 160000",
    )

    draft = await ExtractionNode(HeuristicLLMProvider()).process(item)

    assert draft is not None
    assert draft.title_raw == "LLM Platform Engineer"
    assert draft.work_mode is WorkMode.REMOTE
    assert draft.compensation is not None
    assert draft.compensation.max_amount == 160000


@pytest.mark.asyncio
async def test_gold_samples_regression_fixture() -> None:
    fixture_path = Path("fixtures/extraction/gold_samples.jsonl")
    extractor = ExtractionNode(HeuristicLLMProvider())

    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        raw_item = RawItem.model_validate(record["raw_item"])
        draft = await extractor.process(raw_item)

        assert draft is not None
        assert draft.title_raw == record["expected"]["title"]
        assert draft.work_mode.value == record["expected"]["work_mode"]
