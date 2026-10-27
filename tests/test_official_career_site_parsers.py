from __future__ import annotations

from typing import Any

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.kaspi_jumys import KaspiJumysParser
from job_ftch.infrastructure.sources.site_parsers.trudvsem import TrudvsemParser


class _Response:
    def __init__(self, text: str = "", payload: dict[str, Any] | None = None) -> None:
        self.text = text
        self._payload = payload or {}
        self.url = "https://example.test/"
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.urls: list[str] = []

    async def get(self, url: str, **_: object) -> _Response:
        self.urls.append(url)
        return self.response


@pytest.mark.asyncio
async def test_trudvsem_parser_maps_official_api_vacancy() -> None:
    client = _Client(
        _Response(
            payload={
                "results": {
                    "vacancies": [
                        {
                            "vacancy": {
                                "id": "vac-42",
                                "job-name": "Data engineer",
                                "vac_url": "https://trudvsem.ru/vacancy/card/company/vac-42",
                                "creation-date": "2026-08-24T15:31:03+0300",
                                "company": {"name": "Example"},
                                "region": {"name": "Москва"},
                                "duty": "Build data pipelines",
                            }
                        }
                    ]
                }
            }
        )
    )
    items = [
        item
        async for item in TrudvsemParser().parse(
            CareerSiteSpec(url="https://trudvsem.ru/vacancy/search", limit=1), client
        )
    ]

    assert len(items) == 1
    assert items[0].external_id == "vac-42"
    assert "Build data pipelines" in items[0].text
    assert "text=" not in client.urls[0] or "limit=1" in client.urls[0]


@pytest.mark.asyncio
async def test_kaspi_jumys_parser_emits_unique_ssr_cards() -> None:
    client = _Client(
        _Response(
            """
            <div class="vacancy-listing-item">
              <a href="/a/data-engineer-123?block_code=search_page">Data engineer</a>
              <span>Алматы</span>
            </div>
            <div class="vacancy-listing-item">
              <a href="/a/data-engineer-123?block_code=search_page">duplicate</a>
            </div>
            """
        )
    )
    client.response.url = "https://jumys.kaspi.kz/rabota/vakansii/"
    items = [
        item
        async for item in KaspiJumysParser().parse(
            CareerSiteSpec(url=client.response.url, limit=5), client
        )
    ]

    assert len(items) == 1
    assert items[0].external_id == "123"
    assert str(items[0].url) == "https://jumys.kaspi.kz/a/data-engineer-123"
    assert "Алматы" in items[0].text


@pytest.mark.asyncio
async def test_cloud_ru_parser_emits_listing_cards() -> None:
    from job_ftch.infrastructure.sources.site_parsers.cloud_ru import CloudRuCareerParser

    client = _Client(
        _Response(
            """
            <a href="/career/vacancies/4188602">DevOps (Кибербезопасность)</a>
            <a href="/career/vacancies/4188602">duplicate</a>
            <a href="/career">listing</a>
            """
        )
    )
    items = [
        item
        async for item in CloudRuCareerParser().parse(
            CareerSiteSpec(url="https://cloud.ru/career/vacancies", limit=5),
            client,
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "4188602"
    assert "DevOps" in items[0].text


@pytest.mark.asyncio
async def test_just_ai_parser_reads_wordpress_rest() -> None:
    from job_ftch.infrastructure.sources.site_parsers.just_ai import JustAICareerParser

    class _ListResponse(_Response):
        def json(self) -> list[dict[str, Any]]:  # type: ignore[override]
            return [
                {
                    "id": 403,
                    "slug": "junior-devops-engineer",
                    "link": "https://careers.just-ai.com/vacancy/junior-devops-engineer",
                    "title": {"rendered": "Junior DevOps engineer"},
                    "content": {"rendered": "<p>Kubernetes and CI.</p>"},
                }
            ]

    items = [
        item
        async for item in JustAICareerParser().parse(
            CareerSiteSpec(url="https://careers.just-ai.com/", limit=5),
            _Client(_ListResponse()),
        )
    ]
    assert len(items) == 1
    assert items[0].external_id == "junior-devops-engineer"
    assert "DevOps" in items[0].text


@pytest.mark.asyncio
async def test_kaspi_parser_discovers_http_vacancy_links() -> None:
    from job_ftch.infrastructure.sources.site_parsers.kaspi import KaspiParser

    client = _Client(_Response('<a href="/vacancy/middle-data-engineer">Data engineer</a>'))
    client.response.url = "https://job.kaspi.kz/search"
    urls = await KaspiParser().discover(
        CareerSiteSpec(url="https://job.kaspi.kz/search", limit=5),
        client,
    )
    assert urls == ["https://job.kaspi.kz/vacancy/middle-data-engineer"]
