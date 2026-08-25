from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ("docs", "scripts", "tests", "fixtures")
# README is rendered directly by GitHub and intentionally has no docs front matter.
TARGET_FILES = ("AGENTS.md",)
EXCLUDE_DIRS = {
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    ".venv-release",
    "node_modules",
    "skill",
    "skills",
    ".serena",
    ".claude",
}
REQUIRED_FIELDS = ("title", "description", "updated")
INDEX_NAME = "index.md"


def is_skill_file(path: Path) -> bool:
    if path.name == "SKILL.md":
        return True
    return any(part in {"skill", "skills", ".serena", ".claude"} for part in path.parts)


def iter_markdown_docs(*, include_indexes: bool = False) -> list[Path]:
    files: list[Path] = []
    for rel_path in TARGET_FILES:
        path = ROOT / rel_path
        if path.exists() and path.is_file():
            files.append(path)

    for directory_name in TARGET_DIRS:
        directory = ROOT / directory_name
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.rglob("*.md"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if is_skill_file(path):
                continue
            if not include_indexes and path.name == INDEX_NAME:
                continue
            files.append(path)
    return sorted(set(files))


def parse_front_matter(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return None, "No front matter found"

    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None, "Malformed front matter: no closing ---"

    yaml_text = content[4:end_idx]
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return None, f"YAML parse error: {exc}"
    if not isinstance(data, dict):
        return None, "Front matter is not a dictionary"
    return data, None


def normalize_updated(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None, f"Invalid date format in 'updated' (expected YYYY-MM-DD): {value}"
        return parsed.isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    return None, "Invalid 'updated' field type; expected YYYY-MM-DD string or YAML date"
