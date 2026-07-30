"""Tests for the extruct-based structured-data fallback in json_ld.py."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.sources.scrapers.json_ld import parse_html

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Data Engineer","description":"Build pipelines.",
 "datePosted":"2026-07-01"}
</script>
</head><body></body></html>
"""

_MICRODATA_HTML = """
<html><head><title>ML Engineer | FooBar</title></head>
<body>
<div itemscope itemtype="https://schema.org/JobPosting">
  <span itemprop="title">ML Engineer</span>
  <div itemprop="description">
    We are looking for an ML Engineer to develop production machine learning models.
    You will work with Python, PyTorch, and cloud infrastructure.
  </div>
  <span itemprop="datePosted">2026-07-04</span>
</div>
</body></html>
"""

_OPENGRAPH_HTML = """
<html><head>
  <meta property="og:title" content="DevOps Engineer" />
  <meta property="og:description" content="We are hiring a DevOps Engineer to manage our Kubernetes clusters, CI/CD pipelines, and cloud infrastructure on AWS. Experience with Terraform and Helm is required for this remote position." />
</head><body><p>no main content</p></body></html>
"""

_ARTICLE_OPENGRAPH_HTML = """
<html><head>
  <meta property="og:type" content="article" />
  <meta property="og:title" content="How to hire engineers" />
  <meta property="og:description" content="An editorial guide to hiring engineers, interviewing candidates, setting compensation, and building teams that is intentionally long enough to look like a weak OpenGraph vacancy fallback." />
</head><body></body></html>
"""

_EMPTY_HTML = "<html><head></head><body><p>hello</p></body></html>"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJsonLdParseHtml:
    def test_stdlib_jsonld_path_unchanged(self) -> None:
        result = parse_html(_JSONLD_HTML, url="https://example.com/job/1")
        assert result is not None
        assert result.title == "Data Engineer"
        assert result.description == "Build pipelines."

    def test_microdata_fallback_when_no_jsonld(self) -> None:
        pytest.importorskip("extruct")

        result = parse_html(_MICRODATA_HTML, url="https://example.com/job/2")
        assert result is not None
        assert result.title is not None
        assert "ML Engineer" in (result.title or "")

    def test_opengraph_fallback_with_long_description(self) -> None:
        pytest.importorskip("extruct")

        result = parse_html(_OPENGRAPH_HTML, url="https://example.com/job/3")
        assert result is not None
        assert result.title == "DevOps Engineer"
        assert result.description is not None
        assert len(result.description) >= 120

    def test_opengraph_article_is_not_a_vacancy(self) -> None:
        pytest.importorskip("extruct")

        assert parse_html(_ARTICLE_OPENGRAPH_HTML, url="https://example.com/article/3") is None

    def test_returns_none_on_empty_page(self) -> None:
        result = parse_html(_EMPTY_HTML, url="https://example.com/job/4")
        assert result is None

    def test_extruct_absent_degrades_gracefully(self) -> None:
        import job_ftch.infrastructure.sources.scrapers.json_ld as mod

        original_fn = mod._extruct_fallback

        def _absent(html: str, url: str) -> None:
            return None

        mod._extruct_fallback = _absent  # type: ignore[assignment]
        try:
            result = parse_html(_MICRODATA_HTML, url="https://example.com/job/5")
            # Should return None without raising
            assert result is None
        finally:
            mod._extruct_fallback = original_fn  # type: ignore[assignment]

    def test_jsonld_wins_over_extruct_on_same_page(self) -> None:
        """When JSON-LD is present, extruct must NOT be called."""
        pytest.importorskip("extruct")

        import job_ftch.infrastructure.sources.scrapers.json_ld as mod

        calls: list[str] = []
        original = mod._extruct_fallback

        def _spy(html: str, url: str) -> None:
            calls.append(url)
            return None

        mod._extruct_fallback = _spy  # type: ignore[assignment]
        try:
            result = parse_html(_JSONLD_HTML, url="https://example.com/job/6")
            assert result is not None
            assert calls == [], "extruct should not be called when JSON-LD matches"
        finally:
            mod._extruct_fallback = original  # type: ignore[assignment]
