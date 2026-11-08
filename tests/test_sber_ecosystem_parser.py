from __future__ import annotations

import httpx
import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec


@pytest.mark.asyncio
async def test_sber_ecosystem_discovers_only_real_vacancies_across_pages() -> None:
    requested: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        if request.url.host == "sber.rabota.ru":
            return httpx.Response(
                200,
                text='<a href="https://www.rabota.ru/vacancy/?company_ids=1%2C2'
                '&amp;location.regionId=240&amp;ecosystemSearch=1">ecosystem</a>',
            )
        assert request.url.host == "www.rabota.ru"
        assert request.url.params["company_ids"] == "1,2"
        assert request.url.params["all_regions"] == "1"
        assert "location.regionId" not in request.url.params
        page = request.url.params.get("page", "1")
        html = (
            '<a href="/vacancy/123/">first</a>'
            '<a href="/vacancy/123/?utm_source=x">duplicate</a>'
            '<a href="https://evil.example/vacancy/999/">other host</a>'
            '<a href="/vacancy/">listing</a>'
            if page == "1"
            else '<a href="/vacancy/456/">second</a>'
        )
        return httpx.Response(200, text=html)

    parser = resolve_site_parser("https://sber.rabota.ru/")
    assert parser is not None
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        urls = await parser.discover(CareerSiteSpec(url="https://sber.rabota.ru/", limit=2), client)

    assert urls == ["https://www.rabota.ru/vacancy/123/", "https://www.rabota.ru/vacancy/456/"]
    assert len(requested) == 3


@pytest.mark.asyncio
async def test_sber_ecosystem_rejects_untrusted_or_missing_catalog_link() -> None:
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        return httpx.Response(
            200,
            text='<a href="https://evil.example/vacancy/?company_ids=1&amp;ecosystemSearch=1">bad</a>'
            '<a href="https://www.rabota.ru/vacancy/?company_ids=abc&amp;ecosystemSearch=1">bad</a>',
        )

    parser = resolve_site_parser("https://sber.rabota.ru/")
    assert parser is not None
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await parser.discover(CareerSiteSpec(url="https://sber.rabota.ru/"), client) == []
    assert requested == ["sber.rabota.ru"]


@pytest.mark.asyncio
async def test_sber_ecosystem_search_preserves_keyword_on_catalog() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "sber.rabota.ru":
            return httpx.Response(
                200,
                text='<a href="https://www.rabota.ru/vacancy/?company_ids=1'
                '&amp;ecosystemSearch=1">ecosystem</a>',
            )
        assert request.url.params["query"] == "ML Engineer"
        return httpx.Response(200, text='<a href="/vacancy/123/">job</a>')

    parser = resolve_site_parser("https://sber.rabota.ru/")
    assert parser is not None
    urls = parser.build_search_urls("https://sber.rabota.ru/", ["ML Engineer", "LLM"])
    assert len(urls) == 2
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await parser.discover(CareerSiteSpec(url=urls[0], limit=1), client)
    assert result == ["https://www.rabota.ru/vacancy/123/"]


def test_sber_ecosystem_expands_target_roles_into_searches() -> None:
    from job_ftch.application.search_expansion import expand_career_site_specs

    specs = expand_career_site_specs(
        [CareerSiteSpec(url="https://sber.rabota.ru/")], ["ML Engineer", "LLM Engineer"]
    )
    assert len(specs) == 2
    assert all(isinstance(spec, CareerSiteSpec) and "query=" in spec.url for spec in specs)
