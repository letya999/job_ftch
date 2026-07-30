from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path


def _module():
    path = Path("scripts/capture_dataset.py")
    spec = importlib.util.spec_from_file_location("capture_dataset", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_source_respects_hard_new_item_cap(tmp_path: Path) -> None:
    module = _module()

    class Source:
        async def fetch(self):
            for stable_id in ("a", "b", "c"):
                yield module.RawItem(
                    stable_id=stable_id,
                    source_kind="debug",
                    source_name="test",
                    external_id=stable_id,
                    url=f"https://example.test/{stable_id}",
                    text=f"Vacancy {stable_id}",
                )

    output = tmp_path / "capture.jsonl"
    fetched, new_items = asyncio.run(
        module.capture_source(Source(), output, seen_ids=set(), max_new_items=2)
    )

    assert (fetched, new_items) == (2, 2)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["Vacancy a", "Vacancy b"]
