from __future__ import annotations

import json

import httpx

from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.site_parsers.myjobs import MyJobsParser


class _Client:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("vacancies/v2"):
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"id": 42, "status": "active"}]},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": 42,
                    "title": "Developer",
                    "status": "active",
                    "description": "full detail",
                    "country": {"title": "Georgia", "city": {"title": "Tbilisi"}},
                    "company": {"brand_name": "Example"},
                }
            },
        )


async def test_myjobs_parser_uses_listing_and_full_detail_api() -> None:
    from job_ftch.domain.source_spec import CareerSiteSpec

    client = _Client()
    items = [
        item
        async for item in MyJobsParser().parse(
            CareerSiteSpec(url="https://myjobs.ge/", limit=1), client
        )
    ]

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.CAREER_SITE
    assert items[0].metadata["company"] == "Example"
    assert items[0].metadata["location"] == "Tbilisi"
    assert json.loads(items[0].text)["description"] == "full detail"
    assert client.calls == [
        "https://api.myjobs.ge/api/ka/public/vacancies/v2",
        "https://api.myjobs.ge/api/ka/public/vacancies/42",
    ]
