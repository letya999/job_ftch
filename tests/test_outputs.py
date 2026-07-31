from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from job_ftch.application import builder as builder_module
from job_ftch.application.builder import run_pipeline_from_settings as run_pipeline
from job_ftch.application.pipeline import Pipeline
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import Settings
from job_ftch.domain import Job, RawItem, SourceKind, WorkMode
from job_ftch.domain.presentable import PresentableJob
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sqlite import SQLiteStore
from job_ftch.nodes import SanitizeNode
from job_ftch.nodes.triage import HeuristicTriageNode
from job_ftch.sinks.counted import CountedSink
from job_ftch.sinks.json_file import JsonFileSink
from job_ftch.sinks.telegram_posting import TelegramPostingSink

if TYPE_CHECKING:
    from pathlib import Path


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
async def test_json_sink_omits_repeated_model_state_from_job_metadata(tmp_path: Path) -> None:
    sink = JsonFileSink(tmp_path / "review.jsonl", jsonl=True)
    job = _job().model_copy(
        update={
            "metadata": {
                "ontology_snapshots": {"profile": {"payload_json": "x" * 100_000}},
                "embedding_vector": [0.1] * 1_000,
                "decision_reasons": ["relevance_unknown"],
            }
        }
    )

    await sink.emit(job)
    await sink.flush()

    payload = json.loads((tmp_path / "review.jsonl").read_text(encoding="utf-8"))
    assert payload["metadata"] == {
        "decision_reasons": ["relevance_unknown"],
        "ontology_snapshot_ids": ["profile"],
    }


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
async def test_telegram_posting_sink_formats_presentable_job() -> None:
    client = FakeTelegramClient()
    sink = TelegramPostingSink(client, "target")
    job = _job().model_copy(
        update={
            "presentable": PresentableJob(
                title="Platform Engineer",
                location_formatted="Remote",
                salary_formatted="$100k",
                body="Build systems.",
                contact_section="Apply now",
                tags=("python", "ml"),
                ats_score=0.9,
                language="en",
            )
        }
    )

    await sink.emit(job)

    text = client.sent[0][1]
    assert "LLM Platform Engineer" in text
    assert "Example Corp" in text


@pytest.mark.asyncio
async def test_telegram_posting_sink_batches_and_uses_formatter() -> None:
    client = FakeTelegramClient()
    sink = TelegramPostingSink(
        client,
        "target",
        notify_mode="digest",
        notify_batch_size=1,
        digest_formatter=lambda jobs, start, size: f"{len(jobs)}:{start}:{size}",
    )

    await sink.emit(_job())
    await sink.emit(_job().model_copy(update={"title": "Second"}))
    await sink.flush()

    assert len(client.sent) == 2
    assert client.sent[0][1].endswith("1:0:1")
    assert client.sent[1][1].endswith("1:0:1")


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
    original_build_llm = builder_module.build_llm

    def build_heuristic_llm(settings: Settings):  # type: ignore[no-untyped-def]
        del settings
        return HeuristicLLMProvider()

    builder_module.build_llm = build_heuristic_llm
    settings = Settings.model_validate(
        {
            "source_backend": "local_fixture",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "embedding_enabled": False,
            "embedding_prefilter_enabled": False,
            "bgem3_enabled": False,
            "relevance_backend": "keywords",
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

    try:
        summary = await run_pipeline(settings)
    finally:
        builder_module.build_llm = original_build_llm

    assert summary.fetched == 8
    assert summary.sanitized == 7
    assert summary.triaged == 4
    assert summary.extracted == 4
    assert summary.partial == 2
    # Empty profile catalog is REVIEW; one group item still stays DEFERRED.
    assert summary.review == 4
    assert summary.deferred == 1
    assert summary.rejected == 3
    assert summary.dropped == 3
    assert summary.emitted == 0
    assert summary.posted == 0
    assert summary.drop_reasons == {
        "low_relevance_prefilter": 3,
        "deferred:jobness_unknown": 1,
    }
    assert summary.by_source_kind["telegram_comment"].dropped == 2
    assert summary.by_source_kind["career_site"].extracted == 1
    assert summary.by_source_kind["career_site"].emitted == 0

    output_payload = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert output_payload["items"] == []


@pytest.mark.asyncio
async def test_run_pipeline_from_settings_preserves_precise_pipeline_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "store.db"
    settings = Settings.model_validate(
        {
            "source_backend": "local_fixture",
            "store_backend": "sqlite",
            "store_path": str(db_path),
            "job_group_store_backend": "memory",
            "embedding_enabled": False,
            "embedding_prefilter_enabled": False,
            "bgem3_enabled": False,
            "relevance_backend": "keywords",
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
    connector = SQLiteStore(str(db_path))
    await connector.ping()
    store = TenantStore(settings.tenant_id or "default", connector)
    try:
        assert summary.failed == 0
        assert summary.finished_at is not None
        assert await store.get_run_state("pipeline.status") == "completed"
        assert await store.get_run_state("pipeline.emitted") == str(summary.emitted)
        assert await store.get_run_state("pipeline.finished_at") == summary.finished_at.isoformat()
    finally:
        await connector.close()
    assert len((tmp_path / "review.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    rejected_lines = (tmp_path / "rejected.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rejected_lines) == 3
    rejected_records = [json.loads(line) for line in rejected_lines]
    assert Counter(record["payload"]["reason"] for record in rejected_records) == {
        "low_relevance_prefilter": 3
    }
