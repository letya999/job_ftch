from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from scripts.doc_metadata import INDEX_NAME, ROOT, normalize_updated

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

TARGET_ROOTS = ("docs", "scripts", "tests", "fixtures")
SKIP_DIRS = {
    "__pycache__",
    "__snapshots__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    ".venv-release",
    "node_modules",
}
SKIP_FILE_SUFFIXES = {".pyc", ".pyo"}
TITLE_OVERRIDES = {
    "docs": "Documentation Index",
    "scripts": "Scripts Index",
    "tests": "Tests Index",
    "fixtures": "Fixtures Index",
}


@lru_cache(maxsize=8192)
def _is_gitignored(root: str, relative_path: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            root,
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            relative_path,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _is_ignored_path(path: Path) -> bool:
    try:
        relative_path = path.relative_to(ROOT).as_posix()
    except ValueError:
        return False
    return _is_gitignored(str(ROOT), relative_path)


@dataclass(frozen=True)
class MarkdownFile:
    path: Path
    title: str
    description: str
    updated: str


@dataclass(frozen=True)
class DirectorySummary:
    relative_dir: Path
    child_dirs: tuple[Path, ...]
    markdown_files: tuple[MarkdownFile, ...]
    other_files: tuple[Path, ...]


def parse_front_matter(path: Path) -> dict[str, object]:
    from scripts.doc_metadata import parse_front_matter as parse_doc_front_matter

    data, _ = parse_doc_front_matter(path)
    if not isinstance(data, dict):
        return {}
    return data


def _iter_target_dirs(root: Path) -> Iterable[Path]:
    yield root
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        if _is_ignored_path(path):
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _title_for(path: Path) -> str:
    key = path.as_posix()
    if key in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[key]
    return f"{path.name} Index"


def _summarize_dir(path: Path) -> DirectorySummary:
    child_dirs: list[Path] = []
    markdown_files: list[Path] = []
    other_files: list[Path] = []
    for child in sorted(path.iterdir()):
        if child.name == INDEX_NAME:
            continue
        if _is_ignored_path(child):
            continue
        if child.is_dir():
            if child.name.startswith("."):
                continue
            if child.name in SKIP_DIRS:
                continue
            child_dirs.append(child)
            continue
        if child.suffix in SKIP_FILE_SUFFIXES:
            continue
        if child.suffix == ".md":
            meta = parse_front_matter(child)
            markdown_files.append(
                MarkdownFile(
                    path=child,
                    title=str(meta.get("title", child.name)),
                    description=str(meta.get("description", "")),
                    updated=normalize_updated(meta.get("updated"))[0] or "",
                )
            )
        else:
            other_files.append(child)
    return DirectorySummary(
        relative_dir=path.relative_to(ROOT),
        child_dirs=tuple(child_dirs),
        markdown_files=tuple(markdown_files),
        other_files=tuple(other_files),
    )


def _render_relative_link(base: Path, target: Path) -> str:
    return target.relative_to(base).as_posix()


def _render_summary(summary: DirectorySummary) -> str:
    title = _title_for(summary.relative_dir)
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"`{summary.relative_dir.as_posix()}/`")
    lines.append("")

    if summary.relative_dir.parts[0] == "docs":
        lines.append(
            "Generated index for navigation. Edit source documents, then rerun `uv run python scripts/build_index_docs.py`."
        )
    else:
        lines.append(
            "Generated index for navigation and maintenance. Rerun `uv run python scripts/build_index_docs.py` after structural changes."
        )
    lines.append("")

    if summary.child_dirs:
        lines.append("## Child Indexes")
        lines.append("")
        for child in summary.child_dirs:
            child_index = child / INDEX_NAME
            label = child.name
            rel = _render_relative_link(summary.relative_dir, child_index.relative_to(ROOT))
            lines.append(f"- [{label} index]({rel})")
        lines.append("")

    if summary.markdown_files or summary.other_files:
        lines.append("## Files On This Level")
        lines.append("")
    if summary.markdown_files:
        for md_file in summary.markdown_files:
            rel = _render_relative_link(summary.relative_dir, md_file.path.relative_to(ROOT))
            desc = f" - {md_file.description}" if md_file.description else ""
            upd = f" (Updated: {md_file.updated})" if md_file.updated else ""
            lines.append(f"- [{md_file.title}]({rel}){desc}{upd}")
    if summary.markdown_files and summary.other_files:
        lines.append("")
        lines.append("### Non-Markdown Files")
        lines.append("")
    for file_path in summary.other_files:
        rel = _render_relative_link(summary.relative_dir, file_path.relative_to(ROOT))
        lines.append(f"- [{file_path.name}]({rel})")
    if summary.markdown_files or summary.other_files:
        lines.append("")

    if not summary.child_dirs and not summary.markdown_files and not summary.other_files:
        lines.append("_No tracked files in this directory yet._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_indexes(*, check: bool = False) -> tuple[list[Path], list[Path]]:
    expected: set[Path] = set()
    written: list[Path] = []
    stale: list[Path] = []
    for root_name in TARGET_ROOTS:
        root = ROOT / root_name
        if not root.exists() or not root.is_dir():
            continue
        for directory in _iter_target_dirs(root):
            expected.add(directory / INDEX_NAME)
            summary = _summarize_dir(directory)
            content = _render_summary(summary)
            target = directory / INDEX_NAME
            existing = target.read_text(encoding="utf-8") if target.exists() else None
            if check:
                if existing != content:
                    stale.append(target)
            else:
                target.write_text(content, encoding="utf-8")
                written.append(target)
    for root_name in TARGET_ROOTS:
        root = ROOT / root_name
        if not root.exists() or not root.is_dir():
            continue
        for existing in root.rglob(INDEX_NAME):
            if _is_ignored_path(existing):
                continue
            if existing not in expected and existing.is_file():
                if check:
                    stale.append(existing)
                else:
                    existing.unlink()
    return written, sorted(set(stale))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or validate metadata-aware index.md files.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if any generated index.md would change or should be removed.",
    )
    args = parser.parse_args()

    written, stale = build_indexes(check=args.check)
    if args.check:
        if stale:
            print("Out-of-date index files detected:")
            for path in stale:
                print(path.relative_to(ROOT).as_posix())
            raise SystemExit(1)
        print("All index.md files are up to date.")
        return
    print(f"Generated {len(written)} index files.")
    for path in written:
        print(path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
