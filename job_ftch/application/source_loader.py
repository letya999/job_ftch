"""Load and validate a list of SourceSpec entries from a YAML or JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from job_ftch.domain.source_spec import SourceSpec


def load_sources(path: Path) -> list[SourceSpec]:
    """Read a YAML or JSON file and return validated SourceSpec list."""
    if not isinstance(path, Path):
        path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml

            data: Any = yaml.safe_load(text)

        except ImportError as exc:
            msg = "PyYAML is required to load .yaml source files: pip install pyyaml"
            raise RuntimeError(msg) from exc
    else:
        data = json.loads(text)

    if isinstance(data, dict) and "sources" in data:
        data = data["sources"]

    adapter: TypeAdapter[list[SourceSpec]] = TypeAdapter(list[SourceSpec])
    return adapter.validate_python(data)
