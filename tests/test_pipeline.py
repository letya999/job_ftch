from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from job_ftch.application import Pipeline
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.sinks.fanout import FanOutSink
from job_ftch.sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from syrupy import SnapshotAssertion


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self) -> AsyncIterator[RawItem]:
        async def _items() -> AsyncIterator[RawItem]:
            for item in self._items:
                yield item

        return _items()


class DropSecondNode:
    def __init__(self) -> None:
        self._seen = 0

    async def process(self, item: RawItem) -> RawItem | None:
        self._seen += 1
        if self._seen == 2:
            return None
        return item


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
            "JOB_FTCH_JOB_GROUP_STORE_BACKEND": "memory",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "job_ftch.job.v1"
    assert len(payload["items"]) == 1


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

    assert payload == snapshot
