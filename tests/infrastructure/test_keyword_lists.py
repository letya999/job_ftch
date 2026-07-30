"""Tests for the keyword-list loader used by the post-type classifier."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from job_ftch.infrastructure.classifiers import keyword_lists

if TYPE_CHECKING:
    from collections.abc import Iterator

YAML_PATH = Path(keyword_lists.__file__).resolve().with_name("keyword_lists.yaml")


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    keyword_lists.reset_cache()
    yield
    keyword_lists.reset_cache()


def test_loader_resolves_default_path_to_yaml() -> None:
    assert keyword_lists._resolve_default_path() == str(YAML_PATH)
    assert YAML_PATH.exists()


def test_load_announcement_tokens_has_meetup() -> None:
    tokens = keyword_lists.load_announcement_tokens()
    assert "meetup" in tokens
    assert "webinar" in tokens


def test_load_job_posting_tokens_has_hiring() -> None:
    tokens = keyword_lists.load_job_posting_tokens()
    assert "hiring" in tokens
    assert "senior " in tokens
    assert "вакансия" in tokens


def test_load_candidate_tokens_has_resume_marker() -> None:
    tokens = keyword_lists.load_candidate_tokens()
    assert "#резюме" in tokens
    assert "open to work" in tokens


def test_load_spam_tokens_has_casino() -> None:
    tokens = keyword_lists.load_spam_tokens()
    assert "casino" in tokens
    assert "букмекер" in tokens


def test_loader_returns_lowercased_tokens() -> None:
    """All tokens are lower-cased so the post_type node can do a simple
    casefolded substring check without per-token normalisation."""
    for fn in (
        keyword_lists.load_announcement_tokens,
        keyword_lists.load_job_posting_tokens,
        keyword_lists.load_candidate_tokens,
        keyword_lists.load_spam_tokens,
    ):
        for token in fn():
            assert token == token.lower(), f"non-lowercased token: {token!r}"


def test_loader_uses_mtime_cache(tmp_path: Path) -> None:
    """Calling load_*() twice does not re-read the file (mtime unchanged)."""
    keyword_lists.load_announcement_tokens()  # populates cache
    first_mtime = keyword_lists._CACHE["mtime"]
    keyword_lists.load_announcement_tokens()
    assert keyword_lists._CACHE["mtime"] == first_mtime


def test_reset_cache_clears_state() -> None:
    keyword_lists.load_announcement_tokens()
    assert keyword_lists._CACHE["mtime"] > 0
    keyword_lists.reset_cache()
    assert keyword_lists._CACHE["mtime"] == 0
    assert keyword_lists._CACHE["data"] == {}
