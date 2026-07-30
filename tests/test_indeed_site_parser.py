from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.indeed import (
    IndeedParser,
    _extract_detail_urls,
    _extract_job_keys,
)


def test_extract_job_keys_reads_jk_ids_from_listing_links() -> None:
    html = """
    <a href="/rc/clk?jk=b16f311ae0b6990b&vjs=3">Role A</a>
    <a href="/viewjob?jk=a06375629baf873c">Role B</a>
    """

    keys = _extract_job_keys(html, IndeedParser()._job_key_re(), limit=5)

    assert keys == ["b16f311ae0b6990b", "a06375629baf873c"]


def test_extract_detail_urls_builds_viewjob_links() -> None:
    urls = _extract_detail_urls(
        '<a href="/viewjob?jk=baca07a29703da25">Role</a>',
        "https://www.indeed.com/jobs?q=ai",
        limit=5,
        job_key_re=IndeedParser()._job_key_re(),
    )

    assert urls == ["https://www.indeed.com/viewjob?jk=baca07a29703da25"]


@pytest.mark.asyncio
async def test_indeed_parser_discovers_via_http_when_listing_html_contains_jk() -> None:
    parser = IndeedParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text='<a href="/viewjob?jk=baca07a29703da25">Role</a>',
                url=url,
            )

    spec = CareerSiteSpec(url="https://www.indeed.com/jobs?q=ai", source_name="indeed")
    urls = await parser.discover(spec, _Client())

    assert urls == ["https://www.indeed.com/viewjob?jk=baca07a29703da25"]


@pytest.mark.asyncio
async def test_indeed_parser_falls_back_to_browser_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = IndeedParser()

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> SimpleNamespace:
            del url, follow_redirects
            raise RuntimeError("blocked")

    class _Page:
        url = "https://www.indeed.com/jobs?q=ai"

        async def evaluate(self, expression: str) -> list[str]:
            del expression
            return ["https://www.indeed.com/viewjob?jk=baca07a29703da25"]

        async def content(self) -> str:
            return "<html></html>"

    @asynccontextmanager
    async def _fake_open_page(*args: object, **kwargs: object):
        del args, kwargs
        yield _Page()

    async def _fake_navigate(page: object, url: str, config: dict[str, object]) -> None:
        del page, url, config

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.indeed.open_page", _fake_open_page
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.indeed.navigate", _fake_navigate
    )

    spec = CareerSiteSpec(
        url="https://www.indeed.com/jobs?q=ai",
        source_name="indeed",
        monitor_config={"_bypass_strategy": object()},
    )
    urls = await parser.discover(spec, _Client())

    assert urls == ["https://www.indeed.com/viewjob?jk=baca07a29703da25"]
