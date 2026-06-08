from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.application.pipeline import Pipeline
from job_ftch.domain import RawItem, SourceKind, processed_key_for_raw_item
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.sinks.failure_tolerant import FailureTolerantSink
from job_ftch.sinks.json_file import JsonFileSink


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            for item in self._items:
                yield item

        return _items()


class ExplodingNode:
    async def process(self, item: RawItem) -> RawItem | None:
        if item.external_id == "2":
            raise RuntimeError("boom")
        return item


class ExplodingSink:
    async def emit(self, item: object) -> None:
        del item
        raise OSError("secondary sink emit failed")

    async def flush(self) -> None:
        raise OSError("secondary sink flush failed")


@pytest.mark.asyncio
async def test_pipeline_isolates_unexpected_item_failures_and_tracks_run_state(
    tmp_path: Path,
) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="2", text="two"),
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="3", text="three"),
    ]
    store = InMemoryStore()
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[ExplodingNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
        rejected_sink=JsonFileSink(tmp_path / "rejected.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    rejected = json.loads((tmp_path / "rejected.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert summary.emitted == 2
    assert summary.failed == 1
    assert summary.rejected == 1
    assert summary.by_source_kind["debug"].failed == 1
    assert [item["external_id"] for item in payload] == ["1", "3"]
    assert rejected["reason"] == "item_processing_failed"
    assert rejected["stage"] == "ExplodingNode"
    assert await store.get_run_state("pipeline.status") == "completed_with_failures"
    assert await store.has_processed(processed_key_for_raw_item(items[1])) is True


@pytest.mark.asyncio
async def test_failure_tolerant_sink_swallows_secondary_errors() -> None:
    sink = FailureTolerantSink(ExplodingSink(), sink_name="review")

    await sink.emit({"item": 1})
    await sink.flush()

    assert sink.emit_failures == 1
    assert sink.flush_failures == 1


@pytest.mark.asyncio
async def test_json_file_sink_recovers_staged_array_after_failed_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = JsonFileSink(tmp_path / "jobs.json", schema_version="job_ftch.job.v1")
    await sink.emit({"id": "one"})

    original_replace = Path.replace
    failed_once = {"value": False}

    def flaky_replace(self: Path, target: Path) -> Path:
        if self.name == "jobs.tmp.json" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("disk full during finalize")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full during finalize"):
        await sink.flush()

    monkeypatch.setattr(Path, "replace", original_replace)
    recovered_sink = JsonFileSink(tmp_path / "jobs.json", schema_version="job_ftch.job.v1")
    await recovered_sink.flush()

    payload = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert payload["items"] == [{"id": "one"}]
