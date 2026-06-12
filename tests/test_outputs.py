from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import run_pipeline
from job_ftch.application.pipeline import Pipeline
from job_ftch.config import Settings
from job_ftch.domain import Job, RawItem, SourceKind, WorkMode
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.nodes.triage import HeuristicTriageNode
from job_ftch.sinks.counted import CountedSink
from job_ftch.sinks.json_file import JsonFileSink
from job_ftch.sinks.telegram_posting import TelegramPostingSink


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            for item in self._items:
                yield item

        return _items()


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[object, str]] = []

    async def __aenter__(self) -> FakeTelegramClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    async def send_message(self, entity: object, message: str, **kwargs: object) -> object:
        del kwargs
        self.sent.append((entity, message))
        return object()


def _job() -> Job:
    return Job(
        raw_item_id="raw-1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ai-jobs",
        title="LLM Platform Engineer",
        company="Example Corp",
        description="Build LLM infrastructure and evaluation systems.",
        canonical_url="https://example.com/jobs/1",
        work_mode=WorkMode.REMOTE,
    )


@pytest.mark.asyncio
async def test_json_file_sink_writes_schema_versioned_envelope(tmp_path: Path) -> None:
    sink = JsonFileSink(tmp_path / "jobs.json", schema_version="job_ftch.job.v1")

    await sink.emit(_job())
    await sink.flush()

    payload = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "job_ftch.job.v1"
    assert payload["items"][0]["title"] == "LLM Platform Engineer"


@pytest.mark.asyncio
async def test_jsonl_sink_wraps_payloads_with_schema_version(tmp_path: Path) -> None:
    sink = JsonFileSink(
        tmp_path / "rejected.jsonl", jsonl=True, schema_version="job_ftch.rejected.v1"
    )

    await sink.emit({"reason": "too_short"})
    await sink.flush()

    line = json.loads((tmp_path / "rejected.jsonl").read_text(encoding="utf-8").strip())
    assert line["schema_version"] == "job_ftch.rejected.v1"
    assert line["payload"]["reason"] == "too_short"


@pytest.mark.asyncio
async def test_counted_sink_tracks_source_kind_counts(tmp_path: Path) -> None:
    inner = JsonFileSink(tmp_path / "test-counted.json")
    sink = CountedSink(inner)

    await sink.emit(_job())

    assert sink.emit_count == 1
    assert sink.by_source_kind["telegram_channel"] == 1


@pytest.mark.asyncio
async def test_telegram_posting_sink_flushes_formatted_jobs() -> None:
    client = FakeTelegramClient()
    sink = TelegramPostingSink(client, "target", own_client=True)

    await sink.emit(_job())
    await sink.flush()

    assert len(client.sent) == 1
    assert client.sent[0][0] == "target"
    assert "LLM Platform Engineer" in client.sent[0][1]


@pytest.mark.asyncio
async def test_pipeline_writes_rejected_items_to_separate_sink(tmp_path: Path) -> None:
    items = [
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id="1",
            text="hi",
        )
    ]
    rejected_sink = JsonFileSink(
        tmp_path / "rejected.jsonl",
        jsonl=True,
        schema_version="job_ftch.rejected.v1",
    )
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[HeuristicTriageNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        rejected_sink=rejected_sink,
    )

    summary = await pipeline.run()
    rejected_lines = (tmp_path / "rejected.jsonl").read_text(encoding="utf-8").splitlines()
    rejected = [json.loads(line) for line in rejected_lines]

    assert summary.rejected == 1
    assert rejected[0]["payload"]["reason"] == "too_short"


@pytest.mark.asyncio
async def test_run_pipeline_summary_reports_extracted_review_and_rejected(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "source_backend": "local_fixture",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "embedding_enabled": False,
            "debug_source_path": "fixtures/e2e/multisource_positive.jsonl",
            "output_path": str(tmp_path / "jobs.json"),
            "quarantine_output_path": str(tmp_path / "quarantine.jsonl"),
            "review_output_path": str(tmp_path / "review.jsonl"),
            "rejected_output_path": str(tmp_path / "rejected.jsonl"),
            "pipeline_max_items_per_run": 20,
            "career_site_allowed_hosts": [
                "job-boards.greenhouse.io",
                "www.bcc.kz",
                "bcc.kz",
            ],
        }
    )

    summary = await run_pipeline(settings)

    assert summary.extracted == 5
    assert summary.review >= 1
    assert summary.rejected == 3
    assert summary.posted == 0
    assert summary.by_source_kind["career_site"].extracted == 1
