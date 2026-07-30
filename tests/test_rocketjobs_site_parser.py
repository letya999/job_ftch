from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.rocketjobs import RocketJobsParser


class _Response(SimpleNamespace):
    status_code = 200

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, *, text: str, response_url: str | None = None) -> None:
        self._text = text
        self._response_url = response_url

    async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
        del follow_redirects
        return _Response(text=self._text, url=self._response_url or url)


@pytest.mark.asyncio
async def test_rocketjobs_discovers_offer_detail_urls() -> None:
    client = _Client(
        text="""
                <a href="/oferta-pracy/company-senior-data-engineer-123abc">Role</a>
                <a href="/oferta-pracy/company-senior-data-engineer-123abc?ref=listing#details">Duplicate</a>
                <a href="https://untrusted.example/oferta-pracy/fake-role">External</a>
                <a href="/oferty-pracy/wszystkie-lokalizacje">Listing</a>
                """
    )

    parser = RocketJobsParser()
    spec = CareerSiteSpec(url="https://rocketjobs.pl/", source_name="rocketjobs")

    assert await parser.discover(spec, client) == [
        "https://rocketjobs.pl/oferta-pracy/company-senior-data-engineer-123abc"
    ]


@pytest.mark.asyncio
async def test_rocketjobs_canonicalizes_redirected_detail_page() -> None:
    client = _Client(
        text="unused",
        response_url=("https://www.rocketjobs.pl/oferta-pracy/company-role?ref=campaign#details"),
    )

    parser = RocketJobsParser()
    spec = CareerSiteSpec(
        url="https://www.rocketjobs.pl/oferta-pracy/company-role",
        source_name="rocketjobs",
    )

    assert await parser.discover(spec, client) == [
        "https://www.rocketjobs.pl/oferta-pracy/company-role"
    ]
