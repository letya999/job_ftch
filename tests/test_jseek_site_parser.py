from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import (
    CareerSiteSource,
    DiscoveredCandidate,
    FetchStats,
)
from job_ftch.infrastructure.sources.site_parsers.jseek import (
    JSeekParser,
    _canonical_source_url,
    _extract_detail_action_id,
    _extract_listing_posting_ids,
    _search_query_from_url,
    _source_urls_from_typesense_payload,
)

_POSTING_ID = "770b065f-ceda-4f32-82f7-edc0aa6daae2"
_DUPLICATE_POSTING_ID = "337d492e-ded5-4859-b49c-731952183b88"
_ACTION_ID = "00000000000000000000000000000000"
_SOURCE_URL = "https://www.accenture.com/sg-en/careers/jobdetails?id=R00348851_en"


@dataclass
class _FakeResponse:
    text: str
    url: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del kwargs
        if url.endswith("/chunk.js"):
            return _FakeResponse(
                text=(
                    'let s=(0,a.createServerReference)("'
                    f'{_ACTION_ID}",a.callServer,void 0,a.findSourceMapURL,'
                    '"getPostingDetail"));'
                ),
                url=url,
            )
        return _FakeResponse(
            text=(
                '<html><head><script src="/chunk.js"></script></head><body>'
                r"{\"id\":\"697597a8-8608-45b7-8d1a-f49960b185a1\","
                r"\"name\":\"Accenture\"}"
                r"{\"id\":\""
                f"{_POSTING_ID}"
                r"\",\"title\":\"Advanced AI Full Stack Engineer\","
                r"\"firstSeenAt\":\"$D2026-08-04T14:50:22.000Z\"}"
                r"{\"id\":\""
                f"{_DUPLICATE_POSTING_ID}"
                r"\",\"title\":\"Advanced AI Full Stack Engineer\","
                r"\"firstSeenAt\":\"$D2026-08-04T14:53:13.000Z\"}"
                "</body></html>"
            ),
            url=url,
        )

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse(
            text=(
                '0:{"a":"$@1","f":"","q":"?show='
                f'{_POSTING_ID}","i":false,"b":"test"}}\n'
                '1:{"id":"'
                f'{_POSTING_ID}","title":"Advanced AI Full Stack Engineer",'
                '"sourceUrl":"'
                f'{_SOURCE_URL}&utm_source=jobseek",'
                '"descriptionUrl":"https://jobseek-assets.colophon-group.org/job/'
                f'{_POSTING_ID}/en/latest.html"}}'
            ),
            url=url,
        )


@dataclass
class _JsonResponse:
    payload: dict[str, Any]
    url: str = "https://typesense.colophon-group.org/collections/job_posting/documents/search"
    status_code: int = 200
    text: str = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeSearchClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> _JsonResponse:
        self.calls.append({"url": url, **kwargs})
        if url == "https://jseek.co/api/typesense-key":
            return _JsonResponse(
                {
                    "apiKey": "test-key",  # pragma: allowlist secret
                    "host": "typesense.colophon-group.org",
                    "port": 443,
                    "protocol": "https",
                },
                url=url,
            )
        return _JsonResponse(
            {
                "grouped_hits": [
                    {
                        "hits": [
                            {
                                "document": {
                                    "id": "30919a85-d0fd-4b26-9749-16a842133c42",
                                    "title": "Forward Deployed AI Engineer",
                                    "source_url": "https://jobs.ashbyhq.com/percepta/71478913-02ac-4e0f-b22d-5cf557592b4d?utm_source=jobseek",
                                }
                            },
                            {
                                "document": {
                                    "id": "77385cd2-e574-4387-bdc8-3b1f45fe6f05",
                                    "title": "Applied AI Engineer",
                                    "source_url": "https://jobs.ashbyhq.com/percepta/bbfe8035-2364-419b-bb36-500aaa542fb2",
                                }
                            },
                        ]
                    }
                ]
            },
            url=url,
        )


def test_jseek_helpers_extract_listing_ids_and_action_id() -> None:
    html = (
        r"{\"id\":\"770b065f-ceda-4f32-82f7-edc0aa6daae2\","
        r"\"title\":\"Advanced AI Full Stack Engineer\","
        r"\"firstSeenAt\":\"$D2026-08-04T14:50:22.000Z\"}"
    )
    chunk = (
        f'createServerReference)("{_ACTION_ID}",'
        'a.callServer,void 0,a.findSourceMapURL,"getPostingDetail")'
    )

    assert _extract_listing_posting_ids(html, limit=10) == [_POSTING_ID]
    assert _extract_detail_action_id(chunk) == _ACTION_ID


def test_jseek_canonical_source_url_strips_tracking_only() -> None:
    assert (
        _canonical_source_url(
            "https://WWW.Accenture.com/sg-en/careers/jobdetails?id=R00348851_en"
            "&utm_source=jobseek&utm_medium=aggregator#details"
        )
        == _SOURCE_URL
    )


def test_jseek_search_helpers_build_query_and_urls() -> None:
    parser = JSeekParser()

    assert parser.build_search_urls(
        "https://jseek.co/en/explore",
        ["AI engineer", "AI Engineer"],
    ) == ["https://jseek.co/en/explore?q=AI%2Cengineer"]
    assert _search_query_from_url("https://jseek.co/en/explore?q=AI%2Cengineer") == "AI engineer"
    assert _source_urls_from_typesense_payload(
        {
            "grouped_hits": [
                {
                    "hits": [
                        {
                            "document": {
                                "source_url": "https://jobs.example.com/ai?utm_source=jobseek"
                            }
                        },
                        {"document": {"source_url": "https://jobs.example.com/ai"}},
                    ]
                }
            ]
        },
        limit=5,
    ) == ["https://jobs.example.com/ai"]


@pytest.mark.asyncio
async def test_jseek_discover_returns_deduped_external_source_urls() -> None:
    client = _FakeClient()
    spec = CareerSiteSpec(url="https://jseek.co/en/explore", source_name="jseek", limit=5)

    urls = await JSeekParser().discover(spec, client)

    assert urls == [_SOURCE_URL]
    assert len(client.posts) == 2
    assert client.posts[0]["headers"]["next-action"] == _ACTION_ID
    assert client.posts[0]["content"] == (f'[{{"postingId":"{_POSTING_ID}","locale":"en"}}]')


@pytest.mark.asyncio
async def test_jseek_search_discover_uses_typesense_source_urls() -> None:
    client = _FakeSearchClient()
    spec = CareerSiteSpec(
        url="https://jseek.co/en/explore?q=AI%2Cengineer",
        source_name="jseek",
        limit=5,
    )

    urls = await JSeekParser().discover(spec, client)

    assert urls == [
        "https://jobs.ashbyhq.com/percepta/71478913-02ac-4e0f-b22d-5cf557592b4d",
        "https://jobs.ashbyhq.com/percepta/bbfe8035-2364-419b-bb36-500aaa542fb2",
    ]
    search_call = client.calls[-1]
    assert search_call["headers"]["x-typesense-api-key"] == "test-key"
    assert search_call["params"]["q"] == "AI engineer"
    assert search_call["params"]["query_by"] == "title"


def test_jseek_parser_is_registered() -> None:
    assert isinstance(resolve_site_parser("https://jseek.co/en/explore"), JSeekParser)


@pytest.mark.asyncio
async def test_trusted_jseek_external_urls_bypass_generic_url_ranking() -> None:
    source = object.__new__(CareerSiteSource)
    source.spec = CareerSiteSpec(url="https://jseek.co/en/explore", source_name="jseek")
    source.stats = FetchStats()
    source.store = None
    source._bypass_ctx = None
    source._trusted_parser_urls = {
        "https://people.mcdonalds.co.uk/job-search/location/title/pdx-mc-abc-123",
        "https://www.werkenbijmcdonalds.nl/maintenance-employee/job/P8-274080-2",
    }
    captured: list[str] = []

    async def _iter_scraped_detail_items(
        urls: list[str], scraper_chain: list[str], source_name: str
    ):
        del scraper_chain, source_name
        captured.extend(urls)
        if False:
            yield None

    source._iter_scraped_detail_items = _iter_scraped_detail_items

    candidates = [
        DiscoveredCandidate(
            url="https://people.mcdonalds.co.uk/job-search/location/title/pdx-mc-abc-123"
        ),
        DiscoveredCandidate(
            url="https://www.werkenbijmcdonalds.nl/maintenance-employee/job/P8-274080-2"
        ),
    ]

    items = [item async for item in source._enrich_candidates(candidates, ["dom"], "jseek")]

    assert items == []
    assert captured == [candidate.url for candidate in candidates]
