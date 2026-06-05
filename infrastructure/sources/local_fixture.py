"""Local debug source backed by JSON or JSONL fixtures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from domain import RawItem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class LocalFixtureSource:
    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    async def fetch(self) -> AsyncIterator[RawItem]:
        for payload in self._load_payloads():
            yield RawItem.model_validate(payload)

    def _load_payloads(self) -> list[dict[str, Any]]:
        raw_text = self._fixture_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return []
        if self._fixture_path.suffix == ".jsonl":
            return [json.loads(line) for line in raw_text.splitlines() if line.strip()]
        data = json.loads(raw_text)
        if not isinstance(data, list):
            msg = "Local fixture source expects a JSON array or JSONL file."
            raise ValueError(msg)
        return data
