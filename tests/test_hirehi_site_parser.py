from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirehi import HireHiParser


class _Client:
    async def get(self, url: str, **_: object) -> SimpleNamespace:
        if "/development/" in url:
            return SimpleNamespace(
                url=url,
                text=(
                    '<script type="application/ld+json">'
                    '{"@type":"JobPosting","title":"AI Engineer",'
                    '"description":"<p>Build production AI systems.</p>",'
                    '"hiringOrganization":{"name":"Real Employer"},'
                    '"jobLocation":{"address":{"addressLocality":"London","addressCountry":"RU"}},'
                    '"baseSalary":{"currency":"GBP","value":{"value":2000,"unitText":"MONTH"}},'
                    '"jobLocationType":"TELECOMMUTE"}'
                    "</script>"
                ),
                raise_for_status=lambda: None,
            )
        return SimpleNamespace(
            url=url,
            text=(
                '<script type="application/ld+json">'
                '{"@type":"ItemList","itemListElement":['
                '{"@type":"ListItem","item":{"url":"/development/ai-engineer-in-banking-87396",'
                '"name":"AI Engineer"}}]}'
                "</script>"
            ),
            raise_for_status=lambda: None,
        )


@pytest.mark.asyncio
async def test_hirehi_parser_reads_current_json_ld_listing() -> None:
    spec = CareerSiteSpec(
        url="https://hirehi.ru/?search=AI",
        source_name="hirehi_ru",
        limit=10,
    )

    items = [item async for item in HireHiParser().parse(spec, _Client())]

    assert len(items) == 1
    assert str(items[0].url) == "https://hirehi.ru/development/ai-engineer-in-banking-87396"
    assert "Build production AI systems." in items[0].text
    assert items[0].metadata["company"] == "Real Employer"
    assert items[0].metadata["locations"] == ["London"]
    assert items[0].metadata["source_locations_schema"] == ["London, RU"]
    assert items[0].metadata["base_salary"]["min"] == 2000
    assert items[0].metadata["job_location_type"] == "TELECOMMUTE"


@pytest.mark.asyncio
async def test_hirehi_country_default_is_only_raw_evidence() -> None:
    class CountryOnlyClient(_Client):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            response = await super().get(url, **kwargs)
            response.text = response.text.replace('"addressLocality":"London",', "")
            return response

    spec = CareerSiteSpec(url="https://hirehi.ru/?search=AI", limit=1)
    items = [item async for item in HireHiParser().parse(spec, CountryOnlyClient())]
    assert len(items) == 1
    assert not items[0].metadata.get("locations")
    assert items[0].metadata["source_locations_schema"] == ["RU"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://other.example/development/ai-engineer-in-banking-87396",
        "https://hirehi.ru/development/another-vacancy-12345",
        "https://hirehi.ru/",
    ],
)
async def test_hirehi_rejects_redirect_to_another_page(redirect_url: str) -> None:
    class RedirectClient(_Client):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            response = await super().get(url, **kwargs)
            if "/development/" in url:
                response.url = redirect_url
            return response

    spec = CareerSiteSpec(url="https://hirehi.ru/?search=AI", limit=1)
    assert [item async for item in HireHiParser().parse(spec, RedirectClient())] == []
