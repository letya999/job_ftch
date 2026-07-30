from __future__ import annotations

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.linkedin import LinkedinParser


class _FakeResponse:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def get(
        self, url: str, *, follow_redirects: bool = True, **kwargs: object
    ) -> _FakeResponse:
        del url, follow_redirects, kwargs
        return self._response


@pytest.mark.asyncio
async def test_linkedin_parser_ignores_keyword_suggestion_links() -> None:
    html = """
    <html>
      <body>
        <a href="/jobs/accounting-jobs-almaty?trk=homepage-jobseeker_suggested-search">Accounting jobs</a>
        <a href="/jobs/administrative-jobs-almaty?trk=homepage-jobseeker_suggested-search">Administrative jobs</a>
      </body>
    </html>
    """
    parser = LinkedinParser()
    client = _FakeClient(_FakeResponse(html, "https://example.com/jobs"))
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="linkedin_board")

    items = [item async for item in parser.parse(spec, client)]

    assert items == []


@pytest.mark.asyncio
async def test_linkedin_parser_emits_only_stable_view_links() -> None:
    html = """
    <html>
      <body>
        <a href="/jobs/view/1234567890/">Senior ML Engineer</a>
        <a href="/jobs/accounting-jobs-almaty">Accounting jobs</a>
      </body>
    </html>
    """
    parser = LinkedinParser()
    client = _FakeClient(_FakeResponse(html, "https://example.com/jobs"))
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="linkedin_board")

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url) == "https://example.com/jobs/view/1234567890/"
    assert items[0].metadata["parser"] == "linkedin"
    assert items[0].metadata["source_family"] == "career_site"


@pytest.mark.asyncio
async def test_linkedin_parser_accepts_public_slug_detail_links() -> None:
    html = """
    <html><body>
      <a href="/jobs/view/senior-ml-engineer-at-acme-1234567890?tracking=public">
        Senior ML Engineer
      </a>
    </body></html>
    """
    parser = LinkedinParser()
    client = _FakeClient(_FakeResponse(html, "https://kg.linkedin.com/jobs"))
    spec = CareerSiteSpec(url="https://kg.linkedin.com/jobs", source_name="linkedin_board")

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url) == (
        "https://kg.linkedin.com/jobs/view/senior-ml-engineer-at-acme-1234567890"
    )
