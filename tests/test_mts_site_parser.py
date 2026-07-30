from __future__ import annotations

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.mts import MtsParser


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "data": [
                {
                    "isActive": True,
                    "title": "Senior Data Engineer",
                    "slug": "legacy-123",
                    "description": "Build data products",
                    "publishedAt": "2026-07-19T00:00:00Z",
                }
            ]
        }


class _Client:
    async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
        assert url == "https://job.mts.ru/api/v2/vacancies"
        assert follow_redirects is True
        return _Response()


@pytest.mark.asyncio
async def test_mts_parser_uses_public_vacancies_api() -> None:
    items = [
        item
        async for item in MtsParser().parse(
            CareerSiteSpec(url="https://job.mts.ru/vacancies", limit=1), _Client()
        )
    ]

    assert len(items) == 1
    assert str(items[0].url) == "https://job.mts.ru/vacancies/legacy-123"
    assert items[0].metadata["parser"] == "mts_api"
