from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirehi import HireHiParser


def _json_script(value: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(value)}</script>'


def _job_html(job_id: int) -> str:
    url = f"https://hirehi.ru/development/developer-{job_id}"
    return _json_script(
        {
            "@type": "JobPosting",
            "url": url,
            "title": f"Developer {job_id}",
            "description": f"Build service {job_id}.",
            "hiringOrganization": {"name": "Example Employer"},
        }
    )


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


@pytest.mark.asyncio
async def test_hirehi_walks_overlapping_pages_and_deduplicates_by_job_id() -> None:
    class PagedClient:
        async def get(self, url: str, **_: object) -> SimpleNamespace:
            parsed = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
            page = int(parsed.get("page", "1"))
            start = 1 if page == 1 else 45 if page == 2 else 95
            ids = range(start, start + 50)
            listing = {
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": position,
                        "item": {
                            "url": f"/development/developer-{job_id}",
                            "name": f"Developer {job_id}",
                        },
                    }
                    for position, job_id in enumerate(ids, start=1)
                ],
            }
            body = _json_script(listing) if "/development/" not in url else _job_html(start)
            if "/development/" in url:
                job_id = int(urlparse(url).path.rsplit("-", 1)[-1])
                body = _job_html(job_id)
            return SimpleNamespace(url=url, text=body, raise_for_status=lambda: None)

    spec = CareerSiteSpec(
        url="https://hirehi.ru/?search=developer",
        limit=120,
        monitor_config={"detail_concurrency": 5},
    )
    items = [item async for item in HireHiParser().parse(spec, PagedClient())]

    assert len(items) == 120
    assert len({str(item.url) for item in items}) == 120
    assert all(item.metadata["detail_vacancy_confirmed"] is True for item in items)


@pytest.mark.asyncio
async def test_hirehi_uses_anchor_cards_when_itemlist_is_missing() -> None:
    class AnchorClient:
        async def get(self, url: str, **_: object) -> SimpleNamespace:
            if "/development/" in url:
                body = _job_html(991)
            else:
                body = '<a href="/development/developer-991">Developer 991</a>'
            return SimpleNamespace(url=url, text=body, raise_for_status=lambda: None)

    spec = CareerSiteSpec(url="https://hirehi.ru/?search=developer", limit=1)
    items = [item async for item in HireHiParser().parse(spec, AnchorClient())]

    assert len(items) == 1
    assert str(items[0].url).endswith("developer-991")
    assert items[0].metadata["detail_vacancy_confirmed"] is True


@pytest.mark.asyncio
async def test_hirehi_propagates_listing_429_for_rate_limit_queue() -> None:
    request = httpx.Request("GET", "https://hirehi.ru/?search=developer")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": "17"},
        text="rate limited",
    )

    class RateLimitedClient:
        async def get(self, url: str, **_: object) -> httpx.Response:
            del url
            return response

    spec = CareerSiteSpec(url="https://hirehi.ru/?search=developer", limit=1)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        _ = [item async for item in HireHiParser().parse(spec, RateLimitedClient())]
    assert caught.value.response.status_code == 429
    assert caught.value.response.headers["Retry-After"] == "17"
