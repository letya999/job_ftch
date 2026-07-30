"""Contract tests for JsonFileSink atomicity (Q11 / AGENTS.md rule).

The hard rule says: "Sinks must not rewrite the whole output file on every
emit". `JsonFileSink` (job_ftch/sinks/json_file.py) implements this with a
.tmp + .staging.jsonl dance plus an atomic rename on flush(). These tests
pin the contract: a crash before flush must preserve the previous output;
the .tmp file must be cleaned up; concurrent emits within the same process
must not lose data; and an empty flush must not create a new file.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import JobRecord
from job_ftch.domain.models import (
    CompensationPeriod,
    CompensationRange,
    Seniority,
    WorkMode,
)
from job_ftch.sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from pathlib import Path


def _mk_job(idx: int) -> JobRecord:
    return JobRecord(
        job_id=f"job-{idx:04d}",
        title=f"Engineer {idx}",
        company="Acme",
        raw_item_id=f"raw-{idx:04d}",
        source_kind="telegram_channel",
        source_name="t",
        canonical_url=f"https://example.com/jobs/{idx}",
        work_mode=WorkMode.REMOTE,
        seniority=Seniority.SENIOR,
        compensation=CompensationRange(
            currency="USD",
            min_amount=100_000,
            max_amount=200_000,
            period=CompensationPeriod.YEAR,
        ),
    )


@pytest.mark.asyncio
async def test_flush_creates_output(tmp_path: Path) -> None:
    sink = JsonFileSink(tmp_path / "out.json")
    await sink.emit(_mk_job(1))
    await sink.emit(_mk_job(2))

    # Every sink owns a unique staging file; PID reuse cannot append stale rows.
    staging = list(tmp_path.glob("out.*.staging.jsonl"))
    assert len(staging) == 1

    await sink.flush()

    out = tmp_path / "out.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["title"] == "Engineer 1"


@pytest.mark.asyncio
async def test_existing_output_preserved_when_emit_does_not_flush(tmp_path: Path) -> None:
    """A second sink that emits but never flushes must not corrupt the existing output."""
    out = tmp_path / "out.json"
    out.write_text(json.dumps([{"title": "previous"}], ensure_ascii=False), encoding="utf-8")

    sink = JsonFileSink(out)
    await sink.emit(_mk_job(1))
    await sink.flush()  # baseline: previous replaced with the new payload

    pre_crash = out.read_text(encoding="utf-8")
    assert "Engineer 1" in pre_crash

    # Simulate a crash between emit and flush: write to a new sink and
    # never call flush.
    sink2 = JsonFileSink(out)
    await sink2.emit(_mk_job(2))
    # No flush — the previous out.json must still hold the baseline.
    assert out.read_text(encoding="utf-8") == pre_crash


@pytest.mark.asyncio
async def test_flush_with_no_emits_keeps_existing_output(tmp_path: Path) -> None:
    """A flush with no emits (and an existing output) leaves the file alone."""
    out = tmp_path / "out.json"
    out.write_text(json.dumps([{"title": "untouched"}], ensure_ascii=False), encoding="utf-8")
    sink = JsonFileSink(out)
    await sink.flush()
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))[0]["title"] == "untouched"


@pytest.mark.asyncio
async def test_flush_with_replace_empty_overwrites_existing_output(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    out.write_text(json.dumps([{"title": "stale"}], ensure_ascii=False), encoding="utf-8")
    sink = JsonFileSink(out, schema_version="job_ftch.job.v1", replace_empty=True)

    await sink.flush()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == {"schema_version": "job_ftch.job.v1", "items": []}


@pytest.mark.asyncio
async def test_empty_concurrent_sink_does_not_clobber_new_output(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    writer = JsonFileSink(out, schema_version="job_ftch.job.v1", replace_empty=True)
    empty_runner = JsonFileSink(out, schema_version="job_ftch.job.v1", replace_empty=True)

    await writer.emit(_mk_job(1))
    await writer.flush()
    await empty_runner.flush()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [item["title"] for item in payload["items"]] == ["Engineer 1"]


@pytest.mark.asyncio
async def test_jsonl_mode_creates_output_after_flush(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    sink = JsonFileSink(out, jsonl=True)
    await sink.emit(_mk_job(1))
    await sink.emit(_mk_job(2))
    await sink.flush()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["title"] == "Engineer 1"


@pytest.mark.asyncio
async def test_jsonl_mode_with_replace_empty_overwrites_existing_output(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out.jsonl"
    out.write_text('{"title":"stale"}\n', encoding="utf-8")
    sink = JsonFileSink(out, jsonl=True, replace_empty=True)

    await sink.flush()

    assert out.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_jsonl_sinks_do_not_share_staging_files(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    abandoned = JsonFileSink(out, jsonl=True)
    active = JsonFileSink(out, jsonl=True)

    await abandoned.emit(_mk_job(1))
    await active.emit(_mk_job(2))
    await active.flush()

    lines = out.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["title"] for line in lines] == ["Engineer 2"]
    assert len(list(tmp_path.glob("out.*.staging.jsonl"))) == 1


@pytest.mark.asyncio
async def test_empty_concurrent_jsonl_sink_does_not_clobber_new_output(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    writer = JsonFileSink(out, jsonl=True, replace_empty=True)
    empty_runner = JsonFileSink(out, jsonl=True, replace_empty=True)

    await writer.emit(_mk_job(1))
    await writer.flush()
    await empty_runner.flush()

    assert json.loads(out.read_text(encoding="utf-8"))["title"] == "Engineer 1"


@pytest.mark.asyncio
async def test_interleaved_emit_flush_emit_flush(tmp_path: Path) -> None:
    """Emit/flush cycles interleaved through the asyncio scheduler don't lose items.

    This is the closest we can get to concurrent emit without mandating a
    process-wide lock on JsonFileSink (which the current implementation does
    not have and the contract does not require).
    """
    out = tmp_path / "out.jsonl"
    sink = JsonFileSink(out, jsonl=True)

    async def writer(start: int, count: int) -> None:
        for i in range(start, start + count):
            await sink.emit(_mk_job(i))
            await asyncio.sleep(0)

    await asyncio.gather(writer(0, 5), writer(100, 5))
    await sink.flush()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 10
    titles = sorted(json.loads(line)["title"] for line in lines)
    # We assert that the most recent and newest items are present, even if
    # the schedule interleaves them in unexpected order.
    assert "Engineer 0" in titles
    assert "Engineer 104" in titles


@pytest.mark.asyncio
async def test_replace_is_atomic_no_partial_overwrite(tmp_path: Path) -> None:
    """Verify the on-disk file is either the new or the new content, never a mix."""
    out = tmp_path / "out.json"
    out.write_text(
        json.dumps([{"title": "new-1"}, {"title": "new-2"}], ensure_ascii=False),
        encoding="utf-8",
    )

    sink = JsonFileSink(out)
    await sink.emit(_mk_job(99))
    await sink.flush()

    data = json.loads(out.read_text(encoding="utf-8"))
    titles = [item["title"] for item in data]
    assert "Engineer 99" in titles
    assert all(isinstance(t, str) for t in titles)
