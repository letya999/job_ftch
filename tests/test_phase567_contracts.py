from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import app as app_module
from app import build_output_sinks
from job_ftch.application.drops import RawItemDropped
from job_ftch.config import Settings
from job_ftch.domain import (
    DuplicateRejectionReason,
    Job,
    JobDraft,
    JobRecord,
    SourceKind,
    WorkMode,
)
from job_ftch.infrastructure.llm.openai_provider import OpenAIInstructorLLMProvider
from job_ftch.nodes.extraction_validation import ExtractionValidationNode

if TYPE_CHECKING:
    from pathlib import Path


def _job(**overrides: object) -> Job:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title": "LLM Platform Engineer",
        "company": "Example Corp",
        "description": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.REMOTE,
        "relevance_score": 0.7,
        "quality_score": 0.85,
    }
    payload.update(overrides)
    return Job.model_validate(payload)


def _record(**overrides: object) -> JobRecord:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title": "LLM Platform Engineer",
        "company": "Example Corp",
        "description": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.REMOTE,
        "relevance_score": 0.7,
        "quality_score": 0.85,
    }
    payload.update(overrides)
    return JobRecord.model_validate(payload)


def _draft(**overrides: object) -> JobDraft:
    payload: dict[str, object] = {
        "raw_item_id": "raw-1",
        "source_kind": SourceKind.TELEGRAM_CHANNEL,
        "source_name": "AI Jobs Board",
        "title_raw": "LLM Platform Engineer",
        "company_name_raw": "Example Corp",
        "description_raw": "Build LLM infra, prompt pipelines, and agent evaluation systems.",
        "work_mode": WorkMode.REMOTE,
    }
    payload.update(overrides)
    return JobDraft.model_validate(payload)


@pytest.mark.asyncio
async def test_extraction_validation_marks_missing_location_for_review() -> None:
    node = ExtractionValidationNode()

    job = await node.process(_draft(location_raw=None, review_reasons=()))

    assert job is not None
    assert "missing_location" in job.review_reasons


@pytest.mark.asyncio
async def test_extraction_validation_drops_too_short_descriptions() -> None:
    node = ExtractionValidationNode(min_description_chars=30)

    with pytest.raises(RawItemDropped, match="too short to be useful"):
        await node.process(_draft(description_raw="too short"))


@pytest.mark.asyncio
async def test_openai_provider_forwards_timeout_retry_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        async def create(self, **kwargs: object) -> dict[str, object]:
            captured["create"] = kwargs
            return {"ok": True}

    def fake_from_provider(model: str, **kwargs: object) -> FakeClient:
        captured["provider_model"] = model
        captured["provider_kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr(
        "job_ftch.infrastructure.llm.openai_provider.instructor.from_provider", fake_from_provider
    )

    provider = OpenAIInstructorLLMProvider(
        api_key="test",
        model="gpt-4.1-mini",
        base_url="https://api.example.test",
        timeout_seconds=12.5,
        max_retries=3,
    )
    result = await provider.extract("Extract this", dict)

    assert result == {"ok": True}
    assert captured["provider_model"] == "openai/gpt-4.1-mini"
    assert captured["provider_kwargs"] == {
        "async_client": True,
        "api_key": "test",
        "base_url": "https://api.example.test",
        "timeout": 12.5,
        "max_retries": 3,
    }
    create_kwargs = captured["create"]
    assert isinstance(create_kwargs, dict)
    assert create_kwargs["model"] == "gpt-4.1-mini"
    assert create_kwargs["max_retries"] == 3
    assert create_kwargs["strict"] is True
    assert create_kwargs["messages"][0]["role"] == "system"
    assert create_kwargs["messages"][1]["content"] == "Extract this"


@pytest.mark.asyncio
async def test_build_output_sinks_routes_borderline_jobs_to_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.items: list[JobRecord] = []

        async def emit(self, item: JobRecord) -> None:
            self.items.append(item)

        async def flush(self) -> None:
            return None

    sinks: list[RecordingSink] = []

    def fake_create_sink(settings: Settings, *, quarantine: bool = False) -> RecordingSink:
        del settings, quarantine
        sink = RecordingSink()
        sinks.append(sink)
        return sink

    monkeypatch.setattr(app_module, "create_sink", fake_create_sink)
    settings = Settings.model_validate(
        {
            "review_output_path": str(tmp_path / "review-contract.jsonl"),
            "rejected_output_path": str(tmp_path / "rejected-contract.jsonl"),
        }
    )

    sink, review_sink, posting_sink = build_output_sinks(settings)
    borderline = _record(quality_score=0.4, review_reasons=("partial_extraction",))
    strong = _record(quality_score=0.9, review_reasons=())

    await sink.emit(borderline)
    await sink.emit(strong)
    await sink.flush()

    assert posting_sink is None
    assert review_sink.emit_count == 1
    assert len(sinks) == 2
    assert len(sinks[0].items) == 2
    assert len(sinks[1].items) == 1
    assert sinks[1].items[0].stable_id == borderline.stable_id


@pytest.mark.asyncio
async def test_build_output_sinks_respects_dry_run_for_posting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_calls: list[tuple[str, str]] = []

    class RecordingSink:
        async def emit(self, item: JobRecord) -> None:
            del item

        async def flush(self) -> None:
            return None

    def fake_create_sink(settings: Settings, *, quarantine: bool = False) -> RecordingSink:
        del quarantine
        create_calls.append((settings.sink_backend, str(settings.output_path)))
        return RecordingSink()

    monkeypatch.setattr(app_module, "create_sink", fake_create_sink)
    settings = Settings.model_validate(
        {
            "dry_run": True,
            "posting_backend": "telegram_posting",
            "telegram_publish_entity": "target",
            "telegram_api_id": 1,
            "telegram_api_hash": "hash",
            "review_output_path": str(tmp_path / "review-contract.jsonl"),
            "rejected_output_path": str(tmp_path / "rejected-contract.jsonl"),
        }
    )

    _, _, posting_sink = build_output_sinks(settings)

    assert posting_sink is None
    assert all(backend != "telegram_posting" for backend, _ in create_calls)


@pytest.mark.asyncio
async def test_build_output_sinks_routes_strong_jobs_to_posting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.items: list[JobRecord] = []

        async def emit(self, item: JobRecord) -> None:
            self.items.append(item)

        async def flush(self) -> None:
            return None

    created: list[tuple[str, RecordingSink]] = []

    def fake_create_sink(settings: Settings, *, quarantine: bool = False) -> RecordingSink:
        del quarantine
        sink = RecordingSink()
        created.append((settings.sink_backend, sink))
        return sink

    monkeypatch.setattr(app_module, "create_sink", fake_create_sink)
    settings = Settings.model_validate(
        {
            "posting_backend": "telegram_posting",
            "telegram_publish_entity": "target",
            "telegram_api_id": 1,
            "telegram_api_hash": "hash",
            "review_output_path": str(tmp_path / "review-contract.jsonl"),
            "rejected_output_path": str(tmp_path / "rejected-contract.jsonl"),
        }
    )

    sink, review_sink, posting_sink = build_output_sinks(settings)
    candidate = _record(quality_score=0.91, review_reasons=())

    await sink.emit(candidate)
    await sink.flush()

    assert posting_sink is not None
    assert review_sink.emit_count == 0
    posting_backend_sinks = [sink for backend, sink in created if backend == "telegram_posting"]
    assert len(posting_backend_sinks) == 1
    assert len(posting_backend_sinks[0].items) == 1
    assert posting_backend_sinks[0].items[0].stable_id == candidate.stable_id


@pytest.mark.asyncio
async def test_duplicate_drop_is_reported_as_dedicated_summary_metric(tmp_path: Path) -> None:
    from job_ftch.application.pipeline import Pipeline
    from job_ftch.domain import RawItem
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.nodes import DedupNode, SanitizeNode
    from job_ftch.sinks.json_file import JsonFileSink

    class StubSource:
        def __init__(self, items: list[RawItem]) -> None:
            self._items = items

        def fetch(self):  # type: ignore[no-untyped-def]
            async def _items() -> object:
                for item in self._items:
                    yield item

            return _items()

    items = [
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug-a",
            external_id="1",
            text="AI Infra Engineer",
        ),
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug-b",
            external_id="2",
            text="AI Infra Engineer",
        ),
    ]
    store = InMemoryStore()
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store)],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
    )

    summary = await pipeline.run()

    assert summary.duplicates == 1
    assert summary.by_source_kind["debug"].duplicates == 1
    assert summary.drop_reasons[DuplicateRejectionReason.DUPLICATE_CONTENT.value] == 1
