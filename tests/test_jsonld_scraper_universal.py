"""Hardening tests for the JSON-LD scraper: multi-block/@graph parsing,
control-char escaping, and the bounded soft-403 retry for cold-session WAF.
"""

from __future__ import annotations

import httpx
import pytest

from job_ftch.infrastructure.sources.scrapers import json_ld


def test_parse_html_finds_job_posting_in_graph_array() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "name": "Acme"},
            {
                "@type": "JobPosting",
                "title": "Senior Backend Engineer",
                "description": "Build things.",
                "employmentType": "FULL_TIME"
            }
        ]
    }
    </script>
    </head><body></body></html>
    """

    result = json_ld.parse_html(html)

    assert result is not None
    assert result.title == "Senior Backend Engineer"
    assert result.employment_type == "FULL_TIME"


def test_parse_html_finds_job_posting_in_top_level_array() -> None:
    html = """
    <script type="application/ld+json">
    [
        {"@type": "BreadcrumbList"},
        {"@type": "JobPosting", "title": "Data Analyst", "description": "Analyze data."}
    ]
    </script>
    """

    result = json_ld.parse_html(html)

    assert result is not None
    assert result.title == "Data Analyst"


def test_parse_html_picks_first_job_posting_across_multiple_script_blocks() -> None:
    html = """
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Acme"}
    </script>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "QA Engineer", "description": "Test things."}
    </script>
    """

    result = json_ld.parse_html(html)

    assert result is not None
    assert result.title == "QA Engineer"


def test_parse_html_handles_job_posting_with_type_array() -> None:
    html = """
    <script type="application/ld+json">
    {"@type": ["JobPosting", "Thing"], "title": "SRE", "description": "Keep it up."}
    </script>
    """

    result = json_ld.parse_html(html)

    assert result is not None
    assert result.title == "SRE"


def test_parse_html_escapes_control_chars_inside_json_strings() -> None:
    # Raw newline/tab inside a JSON string value is invalid JSON per spec, but
    # some sites emit it anyway. The scraper must recover via control-char
    # escaping instead of silently dropping the block.
    raw_description = "Line one\nLine two\tTabbed"
    html = (
        '<script type="application/ld+json">\n'
        "{\n"
        '  "@type": "JobPosting",\n'
        '  "title": "Support Engineer",\n'
        f'  "description": "{raw_description}"\n'
        "}\n"
        "</script>"
    )

    result = json_ld.parse_html(html)

    assert result is not None
    assert result.title == "Support Engineer"
    # After escaping raw control chars to \n/\t and re-parsing as JSON, the
    # JSON decoder converts those escapes back into real newline/tab chars.
    assert result.description == raw_description


def test_parse_html_returns_none_when_no_job_posting_present() -> None:
    html = """
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Acme"}
    </script>
    """

    assert json_ld.parse_html(html) is None


def test_can_handle_requires_majority_of_pages_with_job_posting() -> None:
    with_posting = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "X", "description": "Y"}'
        "</script>"
    )
    without_posting = '<script type="application/ld+json">{"@type": "Organization"}</script>'

    assert json_ld.can_handle([with_posting, with_posting, without_posting]) == {}
    assert json_ld.can_handle([without_posting, without_posting, with_posting]) is None


@pytest.mark.asyncio
async def test_scrape_retries_once_on_soft_403_then_succeeds() -> None:
    job_html = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "Retry Success", "description": "d"}'
        "</script>"
    )
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(403, text="blocked")
        return httpx.Response(200, text=job_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        result = await json_ld.scrape("https://example.com/job/1", {}, client)

    assert attempts["count"] == 2
    assert result is not None
    assert result.title == "Retry Success"


@pytest.mark.asyncio
async def test_scrape_gives_up_after_single_bounded_retry_on_persistent_403() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(403, text="blocked")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        result = await json_ld.scrape("https://example.com/job/1", {}, client)

    # Exactly one retry (bounded by _RETRY_403_MAX = 1): initial + 1 retry.
    assert attempts["count"] == 2
    assert result is None


@pytest.mark.asyncio
async def test_scrape_uses_prefetched_html_without_http_call() -> None:
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, text="")

    job_html = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "Prefetched", "description": "d"}'
        "</script>"
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        result = await json_ld.scrape(
            "https://example.com/job/1", {"prefetched_html": job_html}, client
        )

    assert called["count"] == 0
    assert result is not None
    assert result.title == "Prefetched"
