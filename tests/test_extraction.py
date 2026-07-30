from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest

from job_ftch.domain import JobDraft, JobExtractionStatus, PostType, RawItem, SourceKind, WorkMode
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.nodes.extraction import ExtractionNode

ExtractedItem = TypeVar("ExtractedItem")


class ExplodingLLMProvider:
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        del text, schema
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_extraction_node_emits_partial_draft_when_llm_fails() -> None:
    created_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fetched_at = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.CAREER_SITE,
            "source_name": "ClickHouse",
            "external_id": "1",
            "url": "https://job-boards.greenhouse.io/clickhouse/jobs/1",
            "text": "Senior AI Product Engineer\nRemote Europe\nBuild agent tooling",
            "created_at": created_at,
            "fetched_at": fetched_at,
            # Real company comes from source metadata (set by API monitors), never
            # from the source_name slug.
            "metadata": {
                "job_url": "https://job-boards.greenhouse.io/clickhouse/jobs/1",
                "company": "ClickHouse",
            },
        }
    )

    draft = await ExtractionNode(ExplodingLLMProvider()).process(item)

    assert draft is not None
    assert isinstance(draft, JobDraft)
    assert draft.extraction_status is JobExtractionStatus.PARTIAL
    assert draft.company_name_raw == "ClickHouse"
    assert "partial_extraction" in draft.review_reasons
    assert draft.source_record_id == "1"
    assert str(draft.source_url) == "https://job-boards.greenhouse.io/clickhouse/jobs/1"
    assert draft.posted_at == created_at
    assert draft.fetched_at == fetched_at
    assert "llm:ExplodingLLMProvider" in draft.provenance.extraction


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
async def test_extraction_node_does_not_use_generic_career_monitor_name_as_company() -> None:
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.CAREER_SITE,
            "source_name": "dom",
            "external_id": "3",
            "url": "https://example.com/jobs/3",
            "text": "Senior ML Engineer\nRemote\nBuild evaluation systems",
        }
    )

    draft = await ExtractionNode(ExplodingLLMProvider()).process(item)

    assert draft is not None
    assert draft.company_name_raw is None


@pytest.mark.asyncio
async def test_heuristic_triage_mode_preserves_typed_boundary_without_llm() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs",
        external_id="heuristic-1",
        url="https://example.com/jobs/heuristic-1",
        text="LLM Engineer\nBuild and evaluate production RAG services remotely.",
        metadata={
            "company": "Acme",
            "preclassified_post_type": PostType.JOB_POSTING.value,
        },
    )
    node = ExtractionNode(ExplodingLLMProvider())
    node.configure_graph_params({"extraction_mode": "structured_or_heuristic"})

    draft = await node.process(item)

    assert draft is not None
    assert draft.title_raw == "LLM Engineer"
    assert draft.company_name_raw == "Acme"
    assert draft.work_mode is WorkMode.REMOTE
    assert draft.extraction_status is JobExtractionStatus.PARTIAL
    assert draft.post_type is PostType.JOB_POSTING
    assert draft.metadata["extraction_backend"] == "heuristic_triage"
    assert "budget_deferred" not in draft.review_reasons
    assert draft.provenance.extraction == ("heuristic:triage",)


def test_extraction_node_rejects_unknown_graph_mode() -> None:
    node = ExtractionNode(ExplodingLLMProvider())

    with pytest.raises(ValueError, match="unsupported extraction_mode"):
        node.configure_graph_params({"extraction_mode": "magic"})


@pytest.mark.asyncio
async def test_gold_samples_regression_fixture() -> None:
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    fixture_path = _REPO_ROOT / "fixtures" / "extraction" / "gold_samples.jsonl"
    extractor = ExtractionNode(HeuristicLLMProvider())

    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        raw_item = RawItem.model_validate(record["raw_item"])
        draft = await extractor.process(raw_item)

        assert draft is not None
        assert draft.title_raw == record["expected"]["title"]
        assert draft.work_mode.value == record["expected"]["work_mode"]
