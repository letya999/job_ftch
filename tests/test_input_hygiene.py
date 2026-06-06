from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from application import Pipeline
from application.rejections import RawItemRejected
from domain import RawItem, RawItemRejectionReason, SourceKind
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.stores.in_memory import InMemoryStore
from nodes import SanitizeNode
from sinks.json_file import JsonFileSink

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


class FailingSource:
    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            raise RuntimeError("source exploded before first item")
            yield

        return _items()


@pytest.mark.asyncio
async def test_sanitize_node_normalizes_text_url_and_origin_metadata() -> None:
    node = SanitizeNode(allowed_career_site_hosts=("job-boards.greenhouse.io",))
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name=" ClickHouse\u200b ",
        external_id="  6014112004  ",
        url="HTTPS://JOB-BOARDS.GREENHOUSE.IO/CLICKHOUSE/JOBS/6014112004#fragment",
        text="  Senior\u0000 AI\u00a0Engineer \n\n  Remote  ",
        metadata={
            "board_url": "HTTPS://JOB-BOARDS.GREENHOUSE.IO/CLICKHOUSE#jobs",
            "job_url": "HTTPS://JOB-BOARDS.GREENHOUSE.IO/CLICKHOUSE/JOBS/6014112004?gh_jid=6014112004",
        },
    )

    sanitized = await node.process(item)

    assert sanitized is not None
    assert sanitized.source_name == "ClickHouse"
    assert sanitized.external_id == "6014112004"
    assert sanitized.text == "Senior AI Engineer\nRemote"
    assert str(sanitized.url) == "https://job-boards.greenhouse.io/CLICKHOUSE/JOBS/6014112004"
    assert sanitized.metadata["board_url"] == "https://job-boards.greenhouse.io/CLICKHOUSE"


@pytest.mark.asyncio
async def test_sanitize_node_rejects_empty_text_after_normalization() -> None:
    node = SanitizeNode()
    malformed = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="item-1",
        url=None,
        text=" \u0000 ",
        metadata={},
    )

    with pytest.raises(RawItemRejected) as exc_info:
        await node.process(malformed)

    assert exc_info.value.reason == RawItemRejectionReason.EMPTY_TEXT


@pytest.mark.asyncio
async def test_sanitize_node_rejects_empty_source_name_after_normalization() -> None:
    node = SanitizeNode()
    malformed = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name=" \u200b ",
        external_id="item-1",
        url=None,
        text="valid payload",
        metadata={},
    )

    with pytest.raises(RawItemRejected) as exc_info:
        await node.process(malformed)

    assert exc_info.value.reason == RawItemRejectionReason.EMPTY_SOURCE_NAME


@pytest.mark.asyncio
async def test_pipeline_quarantines_disallowed_origin_host(tmp_path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="clickhouse",
        external_id="6014112004",
        url="https://evil.example/jobs/6014112004",
        text="Senior AI Engineer",
    )
    pipeline = Pipeline(
        source=StubSource([item]),
        nodes=[SanitizeNode(allowed_career_site_hosts=("job-boards.greenhouse.io",))],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    quarantine_lines = (tmp_path / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
    quarantine_record = json.loads(quarantine_lines[0])

    assert summary.emitted == 0
    assert summary.dropped == 1
    assert summary.quarantined == 1
    assert quarantine_record["reason"] == RawItemRejectionReason.DISALLOWED_URL_HOST


@pytest.mark.asyncio
async def test_local_fixture_source_routes_invalid_payloads_to_quarantine(tmp_path: Path) -> None:
    fixture = tmp_path / "fixtures.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source_kind": "debug",
                        "source_name": "fixture",
                        "external_id": "ok-1",
                        "text": "valid payload",
                    }
                ),
                json.dumps(
                    {
                        "source_kind": "debug",
                        "source_name": "fixture",
                        "external_id": "",
                        "text": "   ",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    pipeline = Pipeline(
        source=LocalFixtureSource(fixture),
        nodes=[SanitizeNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    emitted = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    quarantine_lines = (tmp_path / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
    quarantine_record = json.loads(quarantine_lines[0])

    assert summary.fetched == 2
    assert summary.emitted == 1
    assert summary.dropped == 1
    assert summary.quarantined == 1
    assert emitted[0]["external_id"] == "ok-1"
    assert quarantine_record["reason"] == RawItemRejectionReason.INVALID_RAW_ITEM
    assert quarantine_record["snapshot"]["record_index"] == 2


@pytest.mark.asyncio
async def test_pipeline_quarantines_source_fetch_failures(tmp_path: Path) -> None:
    pipeline = Pipeline(
        source=FailingSource(),
        nodes=[SanitizeNode()],
        sink=JsonFileSink(tmp_path / "out.json"),
        store=InMemoryStore(),
        quarantine_sink=JsonFileSink(tmp_path / "quarantine.jsonl", jsonl=True),
    )

    summary = await pipeline.run()
    quarantine_lines = (tmp_path / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
    quarantine_record = json.loads(quarantine_lines[0])

    assert summary.fetched == 0
    assert summary.failed == 1
    assert summary.quarantined == 1
    assert quarantine_record["reason"] == RawItemRejectionReason.SOURCE_FETCH_ERROR
