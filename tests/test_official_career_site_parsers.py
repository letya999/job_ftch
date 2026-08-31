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
