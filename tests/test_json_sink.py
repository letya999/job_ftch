from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_json_file_sink_writes_array_on_finalize(tmp_path: Path) -> None:
    output = tmp_path / "items.json"
    sink = JsonFileSink(output)

    await sink.emit({"text": "one"})
    assert not output.exists()

    await sink.finalize()
    await sink.finalize()

    assert json.loads(output.read_text(encoding="utf-8")) == [{"text": "one"}]


@pytest.mark.asyncio
async def test_json_file_sink_writes_jsonl_as_utf8(tmp_path: Path) -> None:
    output = tmp_path / "items.jsonl"
    sink = JsonFileSink(output, jsonl=True)

    await sink.emit({"text": "Вакансия AI инженера"})
    await sink.finalize()

    raw = output.read_text(encoding="utf-8")
    assert "Вакансия" in raw
    assert "\\u0412" not in raw
    assert json.loads(raw) == {"text": "Вакансия AI инженера"}
