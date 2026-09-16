from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hireseeker import HireSeekerParser


def _script(value: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(value)}</script>'


def _detail_html(job_id: int, title: str) -> str:
    return _script(
        {
            "@graph": [
                {"@type": "BreadcrumbList", "itemListElement": []},
                {
                    "@type": "JobPosting",
                    "title": title,
                    "description": f"<p>Build platform {job_id}.</p>",
                    "identifier": {"name": "hh", "value": str(job_id)},
                    "hiringOrganization": {"name": "Example Employer"},
                    "jobLocation": {
                        "address": {
                            "addressLocality": "Moscow",
                            "addressCountry": "RU",
                        }
                    },
                    "jobLocationType": "TELECOMMUTE",
                    "baseSalary": {
                        "currency": "RUB",
                        "value": {"minValue": 100000, "maxValue": 150000, "unitText": "MONTH"},
                    },
                },
            ]
        }
    )


def _listing_html(ids: list[int], *, category_link: str | None = None) -> str:
    cards = "".join(
        f'<div data-vacancy-card="{job_id}">'
        f'<a data-testid="vacancy-title-link" href="/vacancy/{job_id}-developer-{job_id}">'
        f"Developer {job_id}</a> developer</div>"
        for job_id in ids
    )
    link = f'<a href="{category_link}">Frontend</a>' if category_link else ""
    return cards + link


@pytest.mark.asyncio
async def test_hireseeker_filters_cards_and_extracts_graph_detail() -> None:
    class Client:
        async def get(self, url: str, **_: object) -> SimpleNamespace:
            if "/vacancy/" in url:
                job_id = int(urlparse(url).path.split("/vacancy/", 1)[1].split("-", 1)[0])
                body = _detail_html(job_id, f"Developer {job_id}")
            else:
                body = _listing_html([101, 102, 103], category_link="/vacancy-list/frontend")
            return SimpleNamespace(url=url, text=body, raise_for_status=lambda: None)

    spec = CareerSiteSpec(
        url="https://hireseeker.ru/vacancy-list/backend?search=developer",
        limit=2,
        monitor_config={"detail_concurrency": 2},
    )
    items = [item async for item in HireSeekerParser().parse(spec, Client())]

    assert len(items) == 2
    assert {str(item.url) for item in items} == {
        "https://hireseeker.ru/vacancy/101-developer-101",
        "https://hireseeker.ru/vacancy/102-developer-102",
    }
    assert all(item.metadata["detail_vacancy_confirmed"] is True for item in items)
    assert all(
        item.metadata["board_url"] == "https://hireseeker.ru/vacancy-list/backend?search=developer"
        for item in items
    )
    assert all(item.metadata["company"] == "Example Employer" for item in items)
    assert all(item.metadata["source_platform"] == "hh" for item in items)


@pytest.mark.asyncio
async def test_hireseeker_walks_pages_and_categories_until_limit() -> None:
    class Client:
        async def get(self, url: str, **_: object) -> SimpleNamespace:
            if "/vacancy/" in url:
                job_id = int(urlparse(url).path.split("/vacancy/", 1)[1].split("-", 1)[0])
                body = _detail_html(job_id, f"Developer {job_id}")
            else:
                query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
                page = int(query.get("page", "1"))
                if "/frontend" in url:
                    ids = [301, 302] if page == 1 else []
                    body = _listing_html(ids)
                else:
                    ids = list(range(1, 26)) if page == 1 else []
                    body = _listing_html(ids, category_link="/vacancy-list/frontend")
            return SimpleNamespace(url=url, text=body, raise_for_status=lambda: None)

    spec = CareerSiteSpec(
        url="https://hireseeker.ru/vacancy-list/backend?search=developer",
        limit=27,
        monitor_config={"detail_concurrency": 4},
    )
    items = [item async for item in HireSeekerParser().parse(spec, Client())]

    assert len(items) == 27
    assert len({str(item.url) for item in items}) == 27


@pytest.mark.asyncio
async def test_hireseeker_keeps_card_when_detail_is_unavailable() -> None:
    class Client:
        async def get(self, url: str, **_: object) -> SimpleNamespace:
            if "/vacancy/" in url:
                return SimpleNamespace(
                    url=url,
                    text="not found",
                    raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("404")),
                )
            return SimpleNamespace(
                url=url,
                text=_listing_html([700]),
                raise_for_status=lambda: None,
            )

    spec = CareerSiteSpec(url="https://hireseeker.ru/?search=developer", limit=1)
    items = [item async for item in HireSeekerParser().parse(spec, Client())]

    assert len(items) == 1
    assert items[0].metadata["detail_vacancy_confirmed"] is False
    assert items[0].metadata["hireseeker_id"] == "700"


@pytest.mark.asyncio
async def test_hireseeker_propagates_listing_429() -> None:
    request = httpx.Request("GET", "https://hireseeker.ru/vacancy-list/backend")
    response = httpx.Response(429, request=request, headers={"Retry-After": "42"})

    class Client:
        async def get(self, url: str, **_: object) -> httpx.Response:
            del url
            return response

    spec = CareerSiteSpec(url="https://hireseeker.ru/?search=developer", limit=1)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        _ = [item async for item in HireSeekerParser().parse(spec, Client())]
    assert caught.value.response.status_code == 429


def test_hireseeker_builds_local_search_listing_url() -> None:
    urls = HireSeekerParser().build_search_urls("https://hireseeker.ru/", ["developer"])
    assert urls == ["https://hireseeker.ru/vacancy-list/backend?search=developer"]
