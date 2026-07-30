from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirify import (
    HirifyParser,
    _extract_detail_urls,
)


def test_extract_detail_urls_finds_canonical_hirify_jobs() -> None:
    html = """
    <a href="/jobs/711365-software-engineer-genai-silicon-automation">Role</a>
    <a href="/ai-engineering-jobs">Category</a>
    """

    urls = _extract_detail_urls(html, "https://hirify.me/", limit=5)

    assert urls == ["https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"]


@pytest.mark.asyncio
async def test_hirify_parser_discovers_urls_from_listing() -> None:
    parser = HirifyParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text='<a href="/jobs/711365-software-engineer-genai-silicon-automation">Role</a>',
                url=url,
            )

    spec = CareerSiteSpec(url="https://hirify.me/", source_name="hirify")
    urls = await parser.discover(spec, _Client())

    assert urls == ["https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"]
