"""Recorded XML fixtures for the generic sitemap monitor."""

from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors import sitemap

FIXTURES = Path(__file__).parents[3] / "fixtures" / "real_world" / "monitors" / "sitemap"


class _Response:
    def __init__(
        self, text: str, *, status_code: int = 200, content_type: str = "application/xml"
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.content = text.encode()
        self.headers = {"content-type": content_type}


class _Client:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, **_: object) -> _Response:
        self.calls.append(url)
        return self.responses.get(url, _Response("not found", status_code=404))


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_sitemap_helpers_normalize_urls_and_candidates() -> None:
    assert (
        sitemap._strip_utm("https://example.org/jobs/a?utm_source=x&lang=en")
        == "https://example.org/jobs/a?lang=en"
    )
    assert sitemap._is_job_related("https://example.org/careers/a") is True
    assert sitemap._is_job_related("https://example.org/about") is False
    assert sitemap._is_gzip_response("https://example.org/jobs.xml.gz", "text/xml") is True
    assert sitemap._walk_up_candidates("https://example.org/team/jobs/open") == [
        "https://example.org/team/jobs/open/sitemap.xml",
        "https://example.org/team/jobs/sitemap.xml",
        "https://example.org/team/sitemap.xml",
        "https://example.org/sitemap.xml",
    ]
    assert (
        sitemap._common_nonstandard_candidates("https://example.org/jobs")[-1]
        == "https://example.org/sitemaps/sitemap.xml"
    )


def test_sitemap_xml_helpers_handle_junk_namespace_and_gzip() -> None:
    jobs = _text("jobs.xml")
    root = sitemap._parse_xml("server banner\n" + jobs)
    assert root is not None
    assert sitemap._extract_urls(root) == [
        "https://careers.example.org/jobs/platform-engineer?locale=en",
        "https://careers.example.org/careers/data-engineer",
    ]
    assert sitemap._parse_xml("not xml") is None
    assert sitemap._gunzip_data(gzip.compress(jobs.encode())) == jobs
    with pytest.raises(ValueError, match="empty"):
        sitemap._gunzip_data(b"")
    with pytest.raises(ValueError, match="decompression"):
        sitemap._gunzip_data(b"not gzip")


def test_sitemap_cap_prioritizes_job_locators_over_alphabetical_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sitemap, "MAX_URLS", 2)
    urls = [
        "https://example.org/about/a",
        "https://example.org/about/b",
        "https://example.org/jobs/platform-engineer",
    ]

    selected = sorted(
        urls, key=lambda candidate: (not sitemap._is_job_related(candidate), candidate)
    )[  # noqa: E501
        : sitemap.MAX_URLS
    ]

    assert "https://example.org/jobs/platform-engineer" in selected


@pytest.mark.asyncio
async def test_discover_follows_recorded_index_and_robots_fallback() -> None:
    index_url = "https://careers.example.org/sitemap-index.xml"
    jobs_url = "https://careers.example.org/sitemaps/jobs.xml"
    client = _Client(
        {
            "https://careers.example.org/robots.txt": _Response(
                f"User-agent: *\nSitemap: {index_url}\n"
            ),
            index_url: _Response(_text("index.xml")),
            jobs_url: _Response(_text("jobs.xml")),
        }
    )
    spec = SimpleNamespace(url="https://careers.example.org/careers", monitor_config={})

    urls, sitemap_url = await sitemap.discover(spec, client)

    assert sitemap_url == index_url
    assert urls == {
        "https://careers.example.org/jobs/platform-engineer?locale=en",
        "https://careers.example.org/careers/data-engineer",
    }
    assert "https://careers.example.org/robots.txt" in client.calls


@pytest.mark.asyncio
async def test_cached_sitemap_and_can_handle_ignore_bad_responses() -> None:
    cached = "https://careers.example.org/sitemaps/jobs.xml.gz"
    response = _Response("", content_type="application/gzip")
    response.content = gzip.compress(_text("jobs.xml").encode())
    client = _Client({cached: response})
    spec = SimpleNamespace(
        url="https://careers.example.org/careers", monitor_config={"sitemap_url": cached}
    )

    urls, sitemap_url = await sitemap.discover(spec, client)

    assert len(urls) == 2
    assert sitemap_url is None
    assert await sitemap.can_handle("https://careers.example.org/careers", None) is None
    assert await sitemap.can_handle("https://missing.example.org", _Client({})) is None
