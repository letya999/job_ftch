"""JSON and JSONL debug sink."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4

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
        replace_empty: bool = False,
    ) -> None:
        self._output_path = output_path
        instance_id = f"{os.getpid()}.{uuid4().hex}"
        self._tmp_path = self._build_tmp_path(output_path, instance_id)
        self._staging_path = self._build_staging_path(output_path, instance_id)
        self._jsonl = jsonl
        self._schema_version = schema_version
        self._replace_empty = replace_empty
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._initial_output_signature = self._output_signature()

    async def emit(self, item: object) -> None:
        payload = self._serialize(item)
        line = f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n"
        if self._jsonl:
            with self._staging_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        with self._staging_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    async def flush(self) -> None:
        if self._jsonl:
            if not self._staging_path.exists():
                if not self._replace_empty:
                    return
                self._staging_path.write_text("", encoding="utf-8")
            if self._staging_path.stat().st_size == 0 and self._output_path.exists():
                output_changed_during_run = (
                    self._output_signature() != self._initial_output_signature
                )
                if not self._replace_empty or output_changed_during_run:
                    self._staging_path.unlink(missing_ok=True)
                    return
            self._staging_path.replace(self._output_path)
            return
        items = self._load_staged_items()
        if not items and not self._replace_empty:
            self._staging_path.unlink(missing_ok=True)
            return
        if not items and self._output_path.exists():
            output_changed_during_run = self._output_signature() != self._initial_output_signature
            if not self._replace_empty or output_changed_during_run:
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
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                payload["metadata"] = self._compact_metadata(metadata)
        if isinstance(payload, dict) and payload.get("source_identity") is None:
            # Keep the established output schema stable for legacy sources;
            # populated identities remain available for migrated adapters.
            payload.pop("source_identity", None)
        if self._schema_version is None:
            return payload
        if not self._jsonl:
            return payload
        return {"schema_version": self._schema_version, "payload": payload}

    @staticmethod
    def _compact_metadata(metadata: dict[str, object]) -> dict[str, object]:
        """Keep review artifacts diagnostic without serializing model state per row."""
        omitted = {
            "ontology_snapshots",
            "embedding_vector",
            "bgem3_dense",
            "bgem3_sparse",
            "bge_m3_dense",
            "bge_m3_sparse",
        }
        compact = {key: value for key, value in metadata.items() if key not in omitted}
        snapshots = metadata.get("ontology_snapshots")
        if isinstance(snapshots, dict):
            compact["ontology_snapshot_ids"] = sorted(str(key) for key in snapshots)
        return compact

    @staticmethod
    def _build_tmp_path(output_path: Path, instance_id: str) -> Path:
        suffix = "".join(output_path.suffixes)
        stem = output_path.name[: -len(suffix)] if suffix else output_path.name
        return output_path.with_name(f"{stem}.{instance_id}.tmp{suffix}")

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

    def _output_signature(self) -> tuple[int, int] | None:
        if not self._output_path.exists():
            return None
        stat = self._output_path.stat()
        return stat.st_mtime_ns, stat.st_size


@register_sink("json_file")
def _build_json_file_sink(settings: Settings) -> JsonFileSink:
    return JsonFileSink(
        settings.output_path,
        jsonl=settings.output_jsonl,
        schema_version=settings.output_schema_version,
        replace_empty=getattr(settings, "output_replace_empty", False),
    )
