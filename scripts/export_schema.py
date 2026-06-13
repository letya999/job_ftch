"""Export public JSON Schemas for source and canonical job contracts."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from job_ftch.domain import JobDraft, JobGroup, JobRecord, RawItem
from job_ftch.domain.source_spec import SourceSpec


def _write_schema(path: Path, schema: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sources_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)
    outputs = {
        Path("config/sources.schema.json"): (sources_adapter.json_schema(), "SourceSpec"),
        Path("artifacts/schemas/RawItem.schema.json"): (RawItem.model_json_schema(), "RawItem"),
        Path("artifacts/schemas/JobDraft.schema.json"): (JobDraft.model_json_schema(), "JobDraft"),
        Path("artifacts/schemas/JobRecord.schema.json"): (
            JobRecord.model_json_schema(),
            "JobRecord",
        ),
        Path("artifacts/schemas/JobGroup.schema.json"): (JobGroup.model_json_schema(), "JobGroup"),
    }
    for path, (schema, name) in outputs.items():
        schema["$id"] = f"https://job-ftch.dev/schemas/{name}.schema.json"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        _write_schema(path, schema)
        print(f"Schema written to {path}")
