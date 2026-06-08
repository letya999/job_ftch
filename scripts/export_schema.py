"""Export SourceSpec JSON Schema to config/sources.schema.json."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from job_ftch.domain.source_spec import SourceSpec

if __name__ == "__main__":
    adapter = TypeAdapter(SourceSpec)
    schema = adapter.json_schema()
    out = Path("config/sources.schema.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False))
    print(f"Schema written to {out}")
