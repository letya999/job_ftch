from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from application import Pipeline
from domain import RawItem, SourceKind
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.stores.in_memory import InMemoryStore
from nodes import SanitizeNode
from sinks.fanout import FanOutSink
from sinks.json_file import JsonFileSink


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
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
            "app.py",
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
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "job_ftch.job.v1"
    assert len(payload["items"]) == 1
