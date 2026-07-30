from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from job_ftch.application import Pipeline
from job_ftch.domain import QuarantinedRawItem, RawItem, RawItemRejectionReason, SourceKind
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.sinks.fanout import FanOutSink
from job_ftch.sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

    from syrupy import SnapshotAssertion


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self) -> AsyncIterator[RawItem]:
        async def _items() -> AsyncIterator[RawItem]:
            for item in self._items:
                yield item

        return _items()


class PreQuarantinedSource:
    def __init__(self, items: list[QuarantinedRawItem]) -> None:
        self._items = items

    def fetch(self) -> AsyncIterator[QuarantinedRawItem]:
        async def _items() -> AsyncIterator[QuarantinedRawItem]:
            for item in self._items:
                yield item

        return _items()


class ClosableIterator:
    def __init__(self, items: list[RawItem], closed: list[bool]) -> None:
        self._items = items
        self._closed = closed
        self._index = 0

    def __aiter__(self) -> ClosableIterator:
        return self

    async def __anext__(self) -> RawItem:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item

    async def aclose(self) -> None:
        self._closed.append(True)


class ClosableSource:
    def __init__(self, items: list[RawItem], closed: list[bool]) -> None:
        self._items = items
        self._closed = closed

    def fetch(self) -> ClosableIterator:
        return ClosableIterator(self._items, self._closed)


class SlowPassNode:
    """Yields control to let other tasks run, simulating I/O latency."""

    async def process(self, item: RawItem) -> RawItem:
        import asyncio

        await asyncio.sleep(0)
        return item


class DropSecondNode:
    def __init__(self) -> None:
        self._seen = 0

    async def process(self, item: RawItem) -> RawItem | None:
        self._seen += 1
        if self._seen == 2:
            return None
        return item


class TwoChildFanOutNode:
    is_fan_out_stage = True

    class _Child:
        def __init__(self, item: RawItem, suffix: str) -> None:
            self._item = item
            self._suffix = suffix

        def materialize_raw_item(self) -> RawItem:
            return self._item.model_copy(
                update={"external_id": f"{self._item.external_id}-{self._suffix}"}
            )

    async def process(self, item: RawItem) -> tuple[_Child, _Child]:
        return self._Child(item, "a"), self._Child(item, "b")


@pytest.mark.asyncio
async def test_pipeline_happy_path_and_drop_semantics(tmp_path: Path) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="2", text="two"),
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[DropSecondNode()],
        sink=sink,
        store=InMemoryStore(),
    )

    summary = await pipeline.run()
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))

    assert summary.fetched == 2
    assert summary.dropped == 1
    assert summary.emitted == 1
    assert summary.quarantined == 0
    assert summary.failed == 0
    assert payload[0]["external_id"] == "1"


@pytest.mark.asyncio
async def test_concurrent_sink_emit_failure_keeps_completed_with_failures_status(
    tmp_path: Path,
) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="2", text="two"),
    ]
    store = InMemoryStore()

    class FlakySink:
        def __init__(self) -> None:
            self.calls = 0

        async def emit(self, item: object) -> None:
            del item
            self.calls += 1
            if self.calls == 2:
                raise OSError("emit failed")

        async def flush(self) -> None:
            return None

    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=FlakySink(),
        store=store,
        rejected_sink=JsonFileSink(tmp_path / "rejected.jsonl", jsonl=True),
        pipeline_item_concurrency=2,
    )

    summary = await pipeline.run()

    assert summary.emitted == 1
    assert summary.failed == 1
    assert summary.rejected == 1
    assert await store.get_run_state("pipeline.status") == "completed_with_failures"


@pytest.mark.asyncio
async def test_pipeline_secondary_quarantine_emit_failure_keeps_completed_with_failures_status(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()

    class ExplodingQuarantineSink:
        async def emit(self, item: object) -> None:
            del item
            raise OSError("quarantine emit failed")

        async def flush(self) -> None:
            return None

    pipeline = Pipeline(
        source=PreQuarantinedSource(
            [
                QuarantinedRawItem(
                    reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                    details="bad payload",
                    source_kind=str(SourceKind.DEBUG),
                    source_name="debug",
                    snapshot={"raw": "x"},
                )
            ]
        ),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
        quarantine_sink=ExplodingQuarantineSink(),
        rejected_sink=JsonFileSink(tmp_path / "rejected.jsonl", jsonl=True),
    )

    summary = await pipeline.run()

    assert summary.fetched == 1
    assert summary.rejected == 1
    assert summary.failed == 1
    assert await store.get_run_state("pipeline.status") == "completed_with_failures"


@pytest.mark.asyncio
async def test_pipeline_secondary_rejected_flush_failure_keeps_completed_with_failures_status(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()

    class FlushFailRejectedSink:
        async def emit(self, item: object) -> None:
            del item

        async def flush(self) -> None:
            raise OSError("rejected flush failed")

    pipeline = Pipeline(
        source=PreQuarantinedSource(
            [
                QuarantinedRawItem(
                    reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                    details="bad payload",
                    source_kind=str(SourceKind.DEBUG),
                    source_name="debug",
                    snapshot={"raw": "x"},
                )
            ]
        ),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=store,
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
        rejected_sink=FlushFailRejectedSink(),
    )

    summary = await pipeline.run()

    assert summary.fetched == 1
    assert summary.rejected == 1
    assert summary.failed == 1
    assert await store.get_run_state("pipeline.status") == "completed_with_failures"


@pytest.mark.asyncio
async def test_local_fixture_source_and_jsonl_sink(tmp_path: Path) -> None:
    fixture = tmp_path / "fixtures.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source_kind": "debug",
                        "source_name": "fixture",
                        "external_id": "a",
                        "text": "alpha",
                    }
                ),
                json.dumps(
                    {
                        "source_kind": "debug",
                        "source_name": "fixture",
                        "external_id": "b",
                        "text": "beta",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    source = LocalFixtureSource(fixture)
    sink = JsonFileSink(tmp_path / "out.jsonl", jsonl=True)
    pipeline = Pipeline(
        source=source,
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=InMemoryStore(),
    )

    summary = await pipeline.run()
    lines = (tmp_path / "out.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert summary.emitted == 2
    assert summary.quarantined == 0
    assert len(lines) == 2


def test_pipeline_accepts_explicit_sanitize_node_contract(tmp_path: Path) -> None:
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(
            [RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="x")]
        ),
        sanitize_node=SanitizeNode(),
        nodes=[DropSecondNode()],
        sink=sink,
        store=InMemoryStore(),
    )

    assert pipeline is not None


@pytest.mark.asyncio
async def test_pipeline_supports_fanout_sinks(tmp_path: Path) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
    ]
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=FanOutSink([JsonFileSink(left_path), JsonFileSink(right_path)]),
        store=InMemoryStore(),
    )

    summary = await pipeline.run()

    assert summary.emitted == 1
    assert json.loads(left_path.read_text(encoding="utf-8"))[0]["external_id"] == "1"
    assert json.loads(right_path.read_text(encoding="utf-8"))[0]["external_id"] == "1"


@pytest.mark.asyncio
async def test_pipeline_closes_source_iterator_when_stopping_early(tmp_path: Path) -> None:
    closed: list[bool] = []
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="2", text="two"),
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=ClosableSource(items, closed),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=InMemoryStore(),
    )

    summary = await pipeline.run(max_items=1)

    assert summary.emitted == 1
    assert closed == [True]


@pytest.mark.asyncio
async def test_pipeline_concurrent_all_items_emitted(tmp_path: Path) -> None:
    items = [
        RawItem(
            source_kind=SourceKind.DEBUG, source_name="debug", external_id=str(i), text=f"job {i}"
        )
        for i in range(5)
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[SlowPassNode()],
        sink=sink,
        store=InMemoryStore(),
        pipeline_item_concurrency=3,
    )
    summary = await pipeline.run()
    assert summary.emitted == 5
    assert summary.fetched == 5


@pytest.mark.asyncio
async def test_pipeline_concurrent_respects_max_items(tmp_path: Path) -> None:
    items = [
        RawItem(
            source_kind=SourceKind.DEBUG, source_name="debug", external_id=str(i), text=f"job {i}"
        )
        for i in range(10)
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[SlowPassNode()],
        sink=sink,
        store=InMemoryStore(),
        pipeline_item_concurrency=3,
    )
    summary = await pipeline.run(max_items=4)
    # The `pulled` counter caps source items at exactly max_items; all 4 pass.
    assert summary.emitted == 4


@pytest.mark.asyncio
async def test_pipeline_max_items_caps_fanout_children(tmp_path: Path) -> None:
    item = RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="job")
    pipeline = Pipeline(
        source=StubSource([item]),
        sanitize_node=SanitizeNode(),
        nodes=[TwoChildFanOutNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
    )

    summary = await pipeline.run(max_items=2)

    assert summary.fetched == 1
    assert summary.emitted == 1
    assert summary.dropped == 1
    assert summary.drop_reasons["max_items_budget"] == 1


@pytest.mark.asyncio
async def test_pipeline_max_items_does_not_pull_a_source_item_after_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    pulled: list[str] = []

    class TrackingSource:
        async def fetch(self) -> AsyncIterator[RawItem]:
            for external_id in ("1", "2"):
                pulled.append(external_id)
                yield RawItem(
                    source_kind=SourceKind.DEBUG,
                    source_name="debug",
                    external_id=external_id,
                    text="job",
                )

    pipeline = Pipeline(
        source=TrackingSource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
    )

    await pipeline.run(max_items=1)

    assert pulled == ["1"]


@pytest.mark.asyncio
async def test_pipeline_source_failure_keeps_source_identity(tmp_path: Path) -> None:
    class FailingSource:
        source_kind = SourceKind.DEBUG
        source_name = "failed-source"

        async def fetch(self) -> AsyncIterator[RawItem]:
            raise OSError("source unavailable")
            yield  # pragma: no cover

    pipeline = Pipeline(
        source=FailingSource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
    )

    summary = await pipeline.run()

    assert "debug" in summary.by_source_kind
    assert "debug:failed-source" in summary.by_source_id
    assert "unknown" not in summary.by_source_kind


@pytest.mark.asyncio
async def test_pipeline_concurrent_dedups_same_processed_key(tmp_path: Path) -> None:
    """Two items sharing a processed_key must not both emit under concurrency.

    Without the in-run seen-keys guard this races: both workers call
    has_processed() (both False) before either is marked, so both emit.
    """
    duplicate = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="dup", text="same job"
    )
    items = [
        duplicate,
        duplicate.model_copy(),
        RawItem(
            source_kind=SourceKind.DEBUG, source_name="debug", external_id="other", text="other job"
        ),
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[SlowPassNode()],
        sink=sink,
        store=InMemoryStore(),
        pipeline_item_concurrency=3,
    )
    summary = await pipeline.run()
    # One of the two "dup" items is dropped (already_processed); "other" emits.
    # Stats match the sequential path: all 3 pulled items are counted as fetched.
    assert summary.emitted == 2
    assert summary.fetched == 3


@pytest.mark.asyncio
async def test_pipeline_concurrent_node_failure_is_isolated(tmp_path: Path) -> None:
    class ExplodingNode:
        def __init__(self, target_id: str) -> None:
            self._target = target_id

        async def process(self, item: RawItem) -> RawItem:
            import asyncio

            await asyncio.sleep(0)
            if item.external_id == self._target:
                raise RuntimeError("boom")
            return item

    items = [
        RawItem(
            source_kind=SourceKind.DEBUG, source_name="debug", external_id=str(i), text=f"job {i}"
        )
        for i in range(4)
    ]
    sink = JsonFileSink(tmp_path / "out.json")
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[ExplodingNode("2")],
        sink=sink,
        store=InMemoryStore(),
        pipeline_item_concurrency=3,
    )
    summary = await pipeline.run()
    # item "2" fails, the rest emit
    assert summary.emitted == 3
    assert summary.failed == 1


def test_app_runs_local_pipeline_command(tmp_path: Path) -> None:
    output_path = tmp_path / "cli-output.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "job_ftch",
            "--source-path",
            "fixtures/debug/raw_items.json",
            "--output-path",
            str(output_path),
            "--max-items",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "JOB_FTCH_STORE_BACKEND": "memory",
            "JOB_FTCH_JOB_BACKEND": "memory",
            "JOB_FTCH_JOB_GROUP_STORE_BACKEND": "memory",
            "JOB_FTCH_SEARCH_BACKEND": "memory",
            "JOB_FTCH_LLM_BACKEND": "heuristic",
            "JOB_FTCH_VECTOR_BACKEND": "memory",
            "JOB_FTCH_EMBEDDING_ENABLED": "false",
            "JOB_FTCH_BGEM3_ENABLED": "false",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "job_ftch.job.v1"
    # The default empty-profile fixture has unresolved jobness; the new graph
    # defers it instead of emitting an unverified vacancy.
    assert payload["items"] == []


# ---------------------------------------------------------------------------
# FanOutSink failure semantics (P2 — TEST_IMPROVEMENTS.md §10)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fanout_sink_raises_on_first_sink_failure(tmp_path: Path) -> None:
    """FanOutSink is fail-fast: exception from the first sink propagates immediately.

    Design decision: FanOutSink iterates sinks sequentially.  When one raises
    the remaining sinks are NOT called (no silent partial writes).  Callers that
    need best-effort fan-out should wrap individual sinks with their own try/except.
    """
    good_path = tmp_path / "good.json"
    calls: list[str] = []

    class ExplodingSink:
        async def emit(self, item: object) -> None:
            calls.append("exploding")
            raise OSError("disk full")

    class TrackingSink:
        async def emit(self, item: object) -> None:
            calls.append("tracking")

    fanout = FanOutSink([ExplodingSink(), TrackingSink()])  # type: ignore[type-arg]

    with pytest.raises(OSError, match="disk full"):
        await fanout.emit({"id": "1"})

    # ExplodingSink was called; TrackingSink was NOT reached (fail-fast)
    assert calls == ["exploding"]
    # good_path was never written
    assert not good_path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fanout_sink_succeeds_when_all_sinks_healthy(tmp_path: Path) -> None:
    """FanOutSink emits to all sinks when none raise."""
    left_calls: list[object] = []
    right_calls: list[object] = []

    class LeftSink:
        async def emit(self, item: object) -> None:
            left_calls.append(item)

    class RightSink:
        async def emit(self, item: object) -> None:
            right_calls.append(item)

    fanout = FanOutSink([LeftSink(), RightSink()])  # type: ignore[type-arg]
    await fanout.emit({"id": "1"})

    assert left_calls == [{"id": "1"}]
    assert right_calls == [{"id": "1"}]


# ---------------------------------------------------------------------------
# Schema backward compatibility (P3 — TEST_IMPROVEMENTS.md §15)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_json_output_schema_version_is_stable() -> None:
    """Schema version in output must be explicit."""
    from job_ftch.config import Settings

    assert Settings().output_schema_version == "job_ftch.job.v1"


@pytest.mark.integration
def test_job_record_serialization_round_trip() -> None:
    """Serialization of JobRecord to JSON and back must be lossless."""
    from job_ftch.domain import JobRecord, SourceKind, WorkMode

    record = JobRecord(
        raw_item_id="1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="Acme",
        title="ML Engineer",
        company="OpenAI",
        work_mode=WorkMode.REMOTE,
        description="Write code.",
    )
    serialized = record.model_dump(mode="json")
    restored = JobRecord.model_validate(serialized)
    assert restored == record


# ---------------------------------------------------------------------------
# Snapshot test — JSON envelope format (P3 — TEST_IMPROVEMENTS.md §12)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pipeline_json_envelope_matches_snapshot(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """Pin the JSON output envelope format with syrupy.

    Strips volatile fields (stable_id, fetched_at) before comparing so the
    snapshot is deterministic across runs.
    """
    from syrupy import SnapshotAssertion  # noqa: F401 — type-check guard

    items = [
        RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id="snap-1",
            text="Senior Python Engineer remote position in AI company",
        ),
    ]
    out_path = tmp_path / "snap_out.json"
    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(out_path, schema_version="job_ftch.job.v1"),
        store=InMemoryStore(),
    )

    await pipeline.run()

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # Strip volatile fields to keep snapshot deterministic
    for item in payload.get("items", []):
        item.pop("stable_id", None)
        item.pop("fetched_at", None)
        item.get("metadata", {}).pop("source_run_id", None)

    assert payload == snapshot


@pytest.mark.asyncio
async def test_snapshot_filter_processes_items_in_single_run_path(tmp_path: Path) -> None:
    from job_ftch.nodes.snapshot_filter import SnapshotFilterNode

    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
    ]
    store = InMemoryStore()

    pipeline1 = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out1.json"),
        store=store,
        snapshot_filter=SnapshotFilterNode(store, tenant_id="t1", run_id="r1"),
    )
    summary1 = await pipeline1.run()
    assert summary1.emitted == 1

    pipeline2 = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out2.json"),
        store=store,
        snapshot_filter=SnapshotFilterNode(store, tenant_id="t1", run_id="r2"),
    )
    summary2 = await pipeline2.run()
    assert summary2.emitted == 0
    assert summary2.dropped == 1


@pytest.mark.asyncio
async def test_snapshot_save_failure_marks_run_unsuccessful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_ftch.nodes.snapshot_filter import SnapshotFilterNode

    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
    ]
    store = InMemoryStore()
    snapshot_filter = SnapshotFilterNode(store, tenant_id="t1", run_id="r1")

    async def failing_save_and_purge(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk offline")

    monkeypatch.setattr(snapshot_filter, "save_and_purge", failing_save_and_purge)

    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=JsonFileSink(tmp_path / "out1.json"),
        store=store,
        snapshot_filter=snapshot_filter,
    )
    summary = await pipeline.run()

    assert summary.emitted == 1  # Emission succeeded before the save_and_purge crash
    assert summary.failed == 1  # The pipeline caught the crash and marked failure

    status = await store.get_run_state("pipeline.status")
    assert status == "failed"


@pytest.mark.asyncio
async def test_outbox_target_delivery_is_idempotent(tmp_path: Path) -> None:
    from job_ftch.domain import JobRecord, OutboxRecord, OutboxState, WorkMode

    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id="1", text="one"),
    ]
    store = InMemoryStore()

    class TrackedTarget:
        def __init__(self) -> None:
            self.target_id = "my_target"
            self.delivered: list[JobRecord] = []

        async def deliver(self, record: JobRecord) -> None:
            self.delivered.append(record)

    target = TrackedTarget()

    class FanOutToTargetSink:
        async def emit(self, record: JobRecord) -> None:
            pass

        async def register_delivery_targets(self, targets: list[str]) -> list[str]:
            return targets

    class JobRecordNode:
        async def process(self, item: RawItem) -> JobRecord:
            return JobRecord(
                raw_item_id="1",
                source_kind=SourceKind.DEBUG,
                source_name="test",
                title="Software Engineer",
                company="OpenAI",
                work_mode=WorkMode.REMOTE,
                description="Write code.",
            )

    pipeline = Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(),
        nodes=[JobRecordNode()],
        sink=FanOutToTargetSink(),
        store=store,
        delivery_targets=[target],
    )

    # 1. First run, no pending. It will fetch, outbox, emit, and mark delivered.
    await pipeline.run()
    assert len(target.delivered) == 1

    # Check that outbox record is DELIVERED
    pending = await store.list_pending_outbox()
    assert len(pending) == 0

    # Find the record manually since InMemoryStore doesn't expose list all outbox
    # Wait, we can test idempotency by making a pending outbox record explicitly
    job_record = JobRecord(
        raw_item_id="1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="Software Engineer",
        company="OpenAI",
        work_mode=WorkMode.REMOTE,
        description="Write code.",
    )
    outbox_record = OutboxRecord(
        outbox_id="ob-1",
        tenant_id="default",
        sink_name="my_target",
        idempotency_key="i" * 64,
        observation_id="obs-1",
        content_hash="c" * 64,
        decision_version="v1",
        delivery_payload=job_record.model_dump(),
        state=OutboxState.OUTBOXED,
    )
    await store.enqueue_outbox(outbox_record)

    # 2. Run pipeline again. It should recover pending outbox and deliver it.
    await pipeline.run()

    # The source yields 1 item again, but it's dropped by dedup.
    # target.delivered will get the recovered item.
    # Total delivered = 1 (from first run) + 0 (new from source) + 1 (recovered) = 2
    assert len(target.delivered) == 2

    # 3. Third run. The outbox record should be DELIVERED, so it won't be recovered.
    # The source yields 1 item again, dropped by dedup.
    await pipeline.run()

    # Total = 2 + 0 = 2
    assert len(target.delivered) == 2

    pending = await store.list_pending_outbox()
    assert len(pending) == 0
