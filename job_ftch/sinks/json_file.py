"""JSON and JSONL debug sink."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from job_ftch.application.registry import register_sink

if TYPE_CHECKING:
    from pathlib import Path

    from job_ftch.config import Settings


class JsonFileSink:
    def __init__(
        self,
        output_path: Path,
        *,
        jsonl: bool = False,
        schema_version: str | None = None,
    ) -> None:
        self._output_path = output_path
        instance_id = str(os.getpid())
        self._tmp_path = self._build_tmp_path(output_path)
        self._staging_path = self._build_staging_path(output_path, instance_id)
        self._jsonl = jsonl
        self._schema_version = schema_version
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._jsonl and not self._tmp_path.exists():
            self._tmp_path.write_text("", encoding="utf-8")

    async def emit(self, item: object) -> None:
        payload = self._serialize(item)
        line = f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n"
        if self._jsonl:
            with self._tmp_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        with self._staging_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    async def flush(self) -> None:
        if self._jsonl:
            if not self._tmp_path.exists():
                self._tmp_path.write_text("", encoding="utf-8")
            if self._tmp_path.stat().st_size == 0 and self._output_path.exists():
                self._tmp_path.unlink(missing_ok=True)
                return
            self._tmp_path.replace(self._output_path)
            return
        items = self._load_staged_items()
        if not items and self._output_path.exists():
            self._staging_path.unlink(missing_ok=True)
            return
        payload: object = items
        if self._schema_version is not None:
            payload = {"schema_version": self._schema_version, "items": items}
        self._tmp_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._tmp_path.replace(self._output_path)
        if self._staging_path.exists():
            self._staging_path.unlink()

    def _serialize(self, item: object) -> object:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if self._schema_version is None:
            return payload
        if not self._jsonl:
            return payload
        return {"schema_version": self._schema_version, "payload": payload}

    @staticmethod
    def _build_tmp_path(output_path: Path) -> Path:
        suffix = "".join(output_path.suffixes)
        stem = output_path.name[: -len(suffix)] if suffix else output_path.name
        return output_path.with_name(f"{stem}.tmp{suffix}")

    @staticmethod
    def _build_staging_path(output_path: Path, instance_id: str) -> Path:
        suffix = "".join(output_path.suffixes)
        stem = output_path.name[: -len(suffix)] if suffix else output_path.name
        return output_path.with_name(f"{stem}.{instance_id}.staging.jsonl")

    def _load_staged_items(self) -> list[object]:
        if not self._staging_path.exists():
            return []
        return [
            json.loads(line)
            for line in self._staging_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


@register_sink("json_file")
def _build_json_file_sink(settings: Settings) -> JsonFileSink:
    return JsonFileSink(
        settings.output_path,
        jsonl=settings.output_jsonl,
        schema_version=settings.output_schema_version,
    )
