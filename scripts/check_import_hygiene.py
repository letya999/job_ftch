from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_IMPORT_RE = re.compile(
    r"^(?:from|import)\s+(domain|application|nodes|sinks|infrastructure|config)\b",
)
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-release",
    "__pycache__",
}


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> int:
    errors: list[str] = []

    for path in _iter_python_files(ROOT):
        rel = _relative(path)
        if rel.startswith("job_ftch/"):
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if LEGACY_IMPORT_RE.match(line):
                errors.append(f"{rel}:{line_number}: legacy flat import: {line.strip()}")

    if errors:
        print("Import hygiene failed:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("Import hygiene passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
