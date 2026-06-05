"""JSON and JSONL debug sink."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class JsonFileSink:
    def __init__(self, output_path: Path, *, jsonl: bool = False) -> None:
        self._output_path = output_path
        self._jsonl = jsonl
        self._buffer: list[object] = []
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._jsonl:
            self._output_path.write_text("", encoding="utf-8")

    async def emit(self, item: object) -> None:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if self._jsonl:
            with self._output_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n")
            return
        self._buffer.append(payload)
        self._output_path.write_text(
            json.dumps(self._buffer, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
