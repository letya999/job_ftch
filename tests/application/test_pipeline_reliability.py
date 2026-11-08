from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_ftch.application.llm_dispatch import LLMRequestDeferredError
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


class DroppingNode:
    async def process(self, item: RawItem) -> RawItem | None:
        del item
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["llm_pending", "auth", "queue_capacity", "expired"])
async def test_pending_llm_preserves_observation_and_does_not_emit(
    tmp_path: Path, reason: str
) -> None:
    due = datetime.now(UTC) + timedelta(minutes=2)

    class PendingNode:
        async def process(self, item):
            raise LLMRequestDeferredError(
                "request-7", due, reason=reason, requires_operator=reason in {"auth", "expired"}
            )

    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="pending", text="one"
    )
    store = InMemoryStore()
    pipeline = Pipeline(
        source=StubSource([item]),
        sanitize_node=SanitizeNode(),
        nodes=[PendingNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
    )
    summary = await pipeline.run()
    assert summary.completion_state == "waiting_llm"
    assert summary.next_retry_at == due
    assert summary.emitted == 0 and summary.rejected == 0
    assert summary.failed == 0
    assert summary.by_source_kind["debug"].deferred == 1
    assert summary.by_source_id["debug:debug"].deferred == 1
    assert not await store.has_processed(processed_key_for_raw_item(item))
    assert summary.llm_health_error == reason


class ExplodingSink:
    async def emit(self, item: object) -> None:
        del item
        raise OSError("secondary sink emit failed")

    async def flush(self) -> None:
        raise OSError("secondary sink flush failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", [False, True])
async def test_fanout_settles_parent_claim_after_children(tmp_path: Path, waiting: bool) -> None:
    from job_ftch.nodes.dedup import DedupNode

    parent = RawItem(
        source_kind=SourceKind.DEBUG, source_name="digest", external_id="parent", text="digest"
    )
    child = RawItem(
        source_kind=SourceKind.DEBUG, source_name="digest", external_id="child", text="vacancy"
    )
    store = InMemoryStore()

    class Candidate:
        def materialize_raw_item(self):
            return child

    class Split:
        is_fan_out_stage = True

        async def process(self, _):
            return (Candidate(),)

    class Finish:
        async def process(self, item):
            if waiting:
                raise LLMRequestDeferredError("pending", datetime.now(UTC) + timedelta(seconds=30))
            return item

    pipeline = Pipeline(
        source=StubSource([parent]),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store, defer_commit=True), Split(), Finish()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
    )
    result = await pipeline.run()
    assert await store.has_processed(processed_key_for_raw_item(parent)) is (not waiting)
    if waiting:
        other = DedupNode(store, defer_commit=True)
        assert await other.process(parent) is parent
        await other.release_claim(parent.stable_id)
        assert result.emitted == 0 and result.completion_state == "waiting_llm"
    else:
        assert result.emitted == 1 and result.failed == 0


@pytest.mark.asyncio
async def test_reused_pipeline_releases_dedup_on_every_llm_continuation(tmp_path: Path) -> None:
    from job_ftch.nodes.dedup import DedupNode

    class EventuallyReady:
        calls = 0

        async def process(self, item):
            self.calls += 1
            if self.calls <= 2:
                raise LLMRequestDeferredError("pending", datetime.now(UTC) + timedelta(seconds=30))
            return item

    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="retry", text="one"
    )
    store = InMemoryStore()
    stage = EventuallyReady()
    pipeline = Pipeline(
        source=StubSource([item]),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store, defer_commit=True), stage],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
    )
    for _ in range(2):
        summary = await pipeline.run()
        assert summary.completion_state == "waiting_llm"
        assert not await store.has_processed(processed_key_for_raw_item(item))
        another_worker = DedupNode(store, defer_commit=True)
        assert await another_worker.process(item) is item
        await another_worker.release_claim(item.stable_id)
    completed = await pipeline.run()
    assert completed.emitted == 1 and completed.failed == 0
    assert stage.calls == 3
    assert await store.has_processed(processed_key_for_raw_item(item))


@pytest.mark.asyncio
async def test_pending_graph_child_releases_all_claims_from_its_source_item(tmp_path: Path) -> None:
    from job_ftch.application.graph import GraphCompiler
    from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
    from job_ftch.application.graph.executor import GraphExecutor
    from job_ftch.application.graph.pipeline_stage import GraphPipelineStage
    from job_ftch.nodes.dedup import DedupNode

    children = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id=str(n), text=text)
        for n, text in enumerate(["First vacancy", "Second vacancy"])
    ]

    class Candidate:
        def __init__(self, raw):
            self.raw = raw

        def materialize_raw_item(self):
            return self.raw

    class Split:
        is_fan_out_stage = True

        async def process(self, item):
            return tuple(Candidate(child) for child in children)

    class Pass:
        async def process(self, item):
            return item

    class PendingSecond:
        async def process(self, item):
            if item.stable_id == children[1].stable_id:
                raise LLMRequestDeferredError("pending", datetime.now(UTC) + timedelta(seconds=30))
            return item

    graph = GraphCompiler().compile(
        GraphSpec(
            "pending-children",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("split", "candidate_segmentation", after=("sanitize",)),
                NodeSpec("dedup", "dedup", effect="gate", after=("split",)),
                NodeSpec("extract", "extraction", after=("dedup",)),
                NodeSpec("normalize", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal", "legacy_routing", effect="terminal_decision", after=("normalize",)
                ),
            ),
        )
    )
    store = InMemoryStore()
    dedup = DedupNode(store, defer_commit=True)
    unrelated = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="other", text="Other work"
    )
    await dedup.process(unrelated)
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": Pass(),
            "split": Split(),
            "dedup": dedup,
            "extract": PendingSecond(),
            "normalize": Pass(),
            "terminal": Pass(),
        },
    )
    parent = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="digest", text="Digest"
    )
    pipeline = Pipeline(
        source=StubSource([parent]),
        sanitize_node=SanitizeNode(),
        nodes=[GraphPipelineStage(executor)],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
    )
    summary = await pipeline.run()
    assert summary.completion_state == "waiting_llm" and summary.emitted == 0
    assert not await store.has_processed(processed_key_for_raw_item(parent))
    another_worker = DedupNode(store, defer_commit=True)
    for child in children:
        assert await another_worker.process(child) is child
        await another_worker.release_claim(child.stable_id)
    with pytest.raises(RuntimeError, match="dedup claim is held"):
        await another_worker.process(unrelated)
    await dedup.release_claim(unrelated.stable_id)


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
    # A failed item remains retryable and must not be consumed as terminal.
    assert await store.has_processed(processed_key_for_raw_item(items[1])) is False


@pytest.mark.asyncio
async def test_pipeline_records_silent_node_drops_in_rejected_artifact(tmp_path: Path) -> None:
    item = RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one")
    pipeline = Pipeline(
        source=StubSource([item]),
        sanitize_node=SanitizeNode(),
        nodes=[DroppingNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        rejected_sink=JsonFileSink(tmp_path / "rejected.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    rejected = json.loads((tmp_path / "rejected.jsonl").read_text(encoding="utf-8"))

    assert summary.emitted == 0
    assert summary.rejected == 1
    assert rejected["reason"] == "node_returned_none:DroppingNode"
    assert rejected["stage"] == "DroppingNode"


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
        if ".tmp.json" in self.name and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("disk full during finalize")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full during finalize"):
        await sink.flush()

    monkeypatch.setattr(Path, "replace", original_replace)
    # Retry the same run-owned sink. A new sink deliberately owns a different
    # staging file so it cannot ingest rows abandoned by another run/process.
    await sink.flush()

    payload = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert payload["items"] == [{"id": "one"}]
