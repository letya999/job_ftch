from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from scripts import build_index_docs, doc_metadata

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_front_matter_reads_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(
        "---\n"
        'title: "Sample"\n'
        'description: "Example document."\n'
        "updated: 2026-07-24\n"
        "---\n\n"
        "# Body\n",
        encoding="utf-8",
    )

    data, error = doc_metadata.parse_front_matter(path)

    assert error is None
    assert data == {
        "title": "Sample",
        "description": "Example document.",
        "updated": date(2026, 7, 24),
    }


def test_parse_front_matter_reports_missing_closing_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("---\ntitle: Broken\n", encoding="utf-8")

    data, error = doc_metadata.parse_front_matter(path)

    assert data is None
    assert error == "Malformed front matter: no closing ---"


def test_normalize_updated_accepts_string_and_yaml_date() -> None:
    normalized_from_string, error_from_string = doc_metadata.normalize_updated("2026-07-24")
    normalized_from_date, error_from_date = doc_metadata.normalize_updated(date(2026, 7, 24))

    assert (normalized_from_string, error_from_string) == ("2026-07-24", None)
    assert (normalized_from_date, error_from_date) == ("2026-07-24", None)


def test_build_indexes_renders_metadata_and_child_indexes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    docs_dir = root / "docs"
    adr_dir = docs_dir / "adr"
    docs_dir.mkdir()
    adr_dir.mkdir()

    (docs_dir / "overview.md").write_text(
        "---\n"
        'title: "Overview"\n'
        'description: "Top-level project overview."\n'
        "updated: 2026-07-24\n"
        "---\n\n"
        "# Overview\n",
        encoding="utf-8",
    )
    (adr_dir / "adr-0001.md").write_text(
        "---\n"
        'title: "ADR-0001"\n'
        'description: "Decision record."\n'
        "updated: 2026-07-20\n"
        "---\n\n"
        "# ADR\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(build_index_docs, "ROOT", root)
    monkeypatch.setattr(build_index_docs, "TARGET_ROOTS", ("docs",))

    written, stale = build_index_docs.build_indexes()

    root_index = docs_dir / "index.md"
    child_index = adr_dir / "index.md"

    assert stale == []
    assert root_index in written
    assert child_index in written

    root_content = root_index.read_text(encoding="utf-8")
    child_content = child_index.read_text(encoding="utf-8")

    assert "# Documentation Index" in root_content
    assert "[adr index](adr/index.md)" in root_content
    assert (
        "[Overview](overview.md) - Top-level project overview. (Updated: 2026-07-24)"
        in root_content
    )
    assert "# adr Index" in child_content
    assert "[ADR-0001](adr-0001.md) - Decision record. (Updated: 2026-07-20)" in child_content


def test_build_indexes_skips_gitignored_paths(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    docs_dir = root / "fixtures" / "real_world"
    ignored_dir = docs_dir / "failed_parsers_html"
    docs_dir.mkdir(parents=True)
    ignored_dir.mkdir()

    ignored_file = docs_dir / "career_site_ingest_working_urls_abc_20260728.yaml"
    ignored_file.write_text("generated: true\n", encoding="utf-8")
    (ignored_dir / "snapshot.html").write_text("<html></html>\n", encoding="utf-8")
    (docs_dir / "tracked.json").write_text("{}\n", encoding="utf-8")

    def fake_is_ignored_path(path: Path) -> bool:
        return path in (ignored_file, ignored_dir) or ignored_dir in path.parents

    monkeypatch.setattr(build_index_docs, "ROOT", root)
    monkeypatch.setattr(build_index_docs, "TARGET_ROOTS", ("fixtures",))
    monkeypatch.setattr(build_index_docs, "_is_ignored_path", fake_is_ignored_path)

    build_index_docs.build_indexes()

    index_content = (docs_dir / "index.md").read_text(encoding="utf-8")

    assert "career_site_ingest_working_urls_abc_20260728.yaml" not in index_content
    assert "failed_parsers_html" not in index_content
    assert "tracked.json" in index_content
