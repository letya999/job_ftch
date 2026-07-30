"""Tests for the trafilatura maintext fallback scraper."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.sources.scrapers.maintext import _MIN_CHARS, parse_html

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_CAREER_PAGE_HTML = """
<html>
<head><title>Senior Python Engineer | ACME Corp</title></head>
<body>
<header><nav>Home About Jobs Contact</nav></header>
<main>
  <h1>Senior Python Engineer</h1>
  <article>
    <p>We are looking for a Senior Python Engineer to join our remote-first AI platform team.</p>
    <p>You will design and build scalable data pipelines, work closely with ML engineers,
    and contribute to open-source tooling. The role is fully remote with flexible hours.</p>
    <h2>Requirements</h2>
    <ul>
      <li>5+ years of Python experience</li>
      <li>Familiarity with async frameworks (asyncio, FastAPI)</li>
      <li>Experience with cloud infrastructure (AWS or GCP)</li>
    </ul>
    <h2>What we offer</h2>
    <ul>
      <li>Competitive salary</li>
      <li>Equity package</li>
      <li>Health insurance</li>
    </ul>
  </article>
</main>
<footer>Copyright 2026 ACME Corp</footer>
</body>
</html>
"""

_NAV_ONLY_HTML = """
<html><head><title>Jobs</title></head>
<body><nav>Home About Jobs Contact</nav></body>
</html>
"""

_NO_JSONLD_HTML = """
<html><head><title>Backend Engineer | Startup XYZ</title></head>
<body>
<main>
<h1>Backend Engineer</h1>
<p>Startup XYZ is hiring a Backend Engineer. You will work on our core platform,
design REST APIs, and collaborate with a small agile team. We value clean code,
automated testing, and continuous delivery. The position is remote-friendly.</p>
<p>Strong knowledge of Python or Go is required. Experience with Kubernetes is a plus.</p>
</main>
</body>
</html>
"""

_ARTICLE_HTML = """
<html><head>
  <title>How to hire engineers | Startup XYZ</title>
  <meta property="og:type" content="article">
</head><body><main>
  <p>This editorial article discusses hiring engineers, interview processes,
  recruiting strategy, and compensation in enough detail to exceed the main
  text fallback threshold while still not being an open position.</p>
</main></body></html>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseHtml:
    def test_extracts_title_and_description_from_career_page(self) -> None:
        pytest.importorskip("trafilatura")

        result = parse_html(_CAREER_PAGE_HTML)
        assert result is not None
        assert result.title is not None
        assert "Python" in result.title or "Engineer" in result.title
        assert result.description is not None
        assert len(result.description) >= _MIN_CHARS

    def test_returns_none_on_nav_only_page(self) -> None:
        pytest.importorskip("trafilatura")

        result = parse_html(_NAV_ONLY_HTML)
        # Nav-only page has no body text; should return None or have description
        # shorter than _MIN_CHARS — either is acceptable.
        if result is not None:
            assert result.description is None or len(result.description or "") < _MIN_CHARS

    def test_no_structured_fields(self) -> None:
        pytest.importorskip("trafilatura")

        result = parse_html(_NO_JSONLD_HTML)
        assert result is not None
        # Maintext scraper does not populate structured fields
        assert result.locations is None
        assert result.base_salary is None
        assert result.employment_type is None

    def test_rejects_page_explicitly_declared_as_article(self) -> None:
        pytest.importorskip("trafilatura")

        assert parse_html(_ARTICLE_HTML) is None

    def test_parse_html_returns_none_when_trafilatura_absent(self) -> None:
        import job_ftch.infrastructure.sources.scrapers.maintext as mod

        original = mod._TRAFILATURA_AVAILABLE
        try:
            mod._TRAFILATURA_AVAILABLE = False
            result = parse_html(_CAREER_PAGE_HTML)
            assert result is None
        finally:
            mod._TRAFILATURA_AVAILABLE = original


class TestCanHandle:
    def test_returns_dict_for_rich_html(self) -> None:
        from job_ftch.infrastructure.sources.scrapers.maintext import can_handle

        pytest.importorskip("trafilatura")

        result = can_handle([_CAREER_PAGE_HTML])
        assert result == {}

    def test_can_handle_returns_none_when_trafilatura_absent(self) -> None:
        from job_ftch.infrastructure.sources.scrapers import maintext as mod
        from job_ftch.infrastructure.sources.scrapers.maintext import can_handle

        original = mod._TRAFILATURA_AVAILABLE
        try:
            mod._TRAFILATURA_AVAILABLE = False
            assert can_handle([_CAREER_PAGE_HTML]) is None
        finally:
            mod._TRAFILATURA_AVAILABLE = original
