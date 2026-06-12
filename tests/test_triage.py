from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.application import Pipeline
from job_ftch.domain import RawItem, SourceKind, TriageRejectionReason
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.nodes.triage import HeuristicTriageNode
from job_ftch.sinks.json_file import JsonFileSink


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            for item in self._items:
                yield item

        return _items()


@pytest.mark.asyncio
async def test_triage_drops_low_signal_items_with_stable_reasons(tmp_path: Path) -> None:
    items = [
        RawItem(
            source_kind=SourceKind.TELEGRAM_GROUP,
            source_name="AI Community",
            external_id="group-1",
            url="https://t.me/ai_community/1",
            text="Subscribe for our weekly webinar digest",
        ),
        RawItem(
            source_kind=SourceKind.TELEGRAM_COMMENT,
            source_name="AI Comments",
            external_id="comment-1",
            url="https://t.me/ai_jobs/2",
            text="Thanks everyone for joining today",
            metadata={"post_url": "https://t.me/ai_jobs/1"},
        ),
        RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name="ClickHouse",
            external_id="career-1",
            url="https://job-boards.greenhouse.io/clickhouse/culture",
            text="About us\nOur culture\nCompany values",
            metadata={"board_url": "https://job-boards.greenhouse.io/clickhouse"},
        ),
    ]
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(allowed_career_site_hosts=("job-boards.greenhouse.io",)),
        nodes=[HeuristicTriageNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
    )

    summary = await pipeline.run()

    assert summary.fetched == 3
    assert summary.sanitized == 3
    assert summary.triaged == 0
    assert summary.emitted == 0
    assert summary.quarantined == 0
    assert summary.drop_reasons == {
        TriageRejectionReason.IRRELEVANT_CONTENT: 1,
        TriageRejectionReason.TELEGRAM_LOW_SIGNAL: 1,
        TriageRejectionReason.CAREER_SITE_NON_JOB_PAGE: 1,
    }


@pytest.mark.asyncio
async def test_pipeline_reports_stage_conversion_by_source_kind(tmp_path: Path) -> None:
    fixture = Path("fixtures/e2e/multisource_positive.jsonl")
    pipeline = Pipeline(
        source=LocalFixtureSource(fixture),
        sanitize_node=SanitizeNode(
            allowed_career_site_hosts=("job-boards.greenhouse.io", "www.bcc.kz", "bcc.kz")
        ),
        nodes=[HeuristicTriageNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    emitted = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))

    assert len(emitted) == 8
    assert summary.fetched == 8
    assert summary.sanitized == 8
    assert summary.triaged == 8
    assert summary.emitted == 8
    assert summary.dropped == 0
    assert summary.quarantined == 0
    assert summary.by_source_kind["telegram_channel"].triaged == 2
    assert summary.by_source_kind["telegram_group"].triaged == 2
    assert summary.by_source_kind["telegram_comment"].triaged == 2
    assert summary.by_source_kind["career_site"].triaged == 2
