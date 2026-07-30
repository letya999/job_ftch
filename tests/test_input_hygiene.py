from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from job_ftch.application import Pipeline
from job_ftch.application.rejections import RawItemRejected
from job_ftch.domain import RawItem, RawItemRejectionReason, SourceKind
from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.sinks.json_file import JsonFileSink

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
async def test_sanitize_node_truncates_overlong_text() -> None:
    node = SanitizeNode(max_text_length=12)
    malformed = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="item-1",
        url=None,
        text="0123456789ABCDEF",
        metadata={},
    )

    result = await node.process(malformed)
    assert result is not None
    assert len(result.text) <= 12


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
        sanitize_node=SanitizeNode(allowed_career_site_hosts=("job-boards.greenhouse.io",)),
        nodes=[],
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
async def test_sanitize_node_allows_career_site_subdomains() -> None:
    node = SanitizeNode(allowed_career_site_hosts=("yandex.cloud",))
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="Yandex Cloud",
        external_id="job-1",
        url="https://kz.console.yandex.cloud/jobs/job-1",
        text="ML Engineer role with enough text to survive sanitation.",
    )

    sanitized = await node.process(item)

    assert sanitized is not None
    assert str(sanitized.url) == "https://kz.console.yandex.cloud/jobs/job-1"


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
        sanitize_node=SanitizeNode(),
        nodes=[],
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
        sanitize_node=SanitizeNode(),
        nodes=[],
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


# ---------------------------------------------------------------------------
# Boundary tests (P2 — from TEST_IMPROVEMENTS.md §8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "length,expected_len",
    [
        (11, 11),  # under limit: unchanged
        (12, 12),  # exactly at limit: unchanged
        (13, 12),  # over limit: truncated to max_text_length
    ],
)
@pytest.mark.asyncio
async def test_sanitize_node_text_length_boundary(length: int, expected_len: int) -> None:
    """SanitizeNode truncates overlong text instead of rejecting it."""
    node = SanitizeNode(max_text_length=12)
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="1",
        url=None,
        text="x" * length,
        metadata={},
    )
    result = await node.process(item)
    assert result is not None
    assert len(result.text) <= expected_len


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sanitize_node_url_with_port_allowed() -> None:
    """URLs containing explicit port numbers are accepted when host is whitelisted."""
    node = SanitizeNode(allowed_career_site_hosts=("careers.example.com",))
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="Example",
        external_id="1",
        url="https://careers.example.com:443/job/1",
        text="Valid job description here with enough content.",
    )
    result = await node.process(item)
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sanitize_node_rejects_invisible_only_external_id() -> None:
    """external_id consisting solely of invisible Unicode characters is rejected."""
    node = SanitizeNode()
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="\u200b\u00a0",
        url=None,
        text="valid text that is long enough",
        metadata={},
    )
    with pytest.raises(RawItemRejected):
        await node.process(item)
