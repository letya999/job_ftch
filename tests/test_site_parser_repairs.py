from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.monitors.greenhouse import can_handle as greenhouse_can_handle
from job_ftch.infrastructure.sources.monitors.lever import can_handle as lever_can_handle
from job_ftch.infrastructure.sources.site_parsers.geekjob import GeekJobParser
from job_ftch.infrastructure.sources.site_parsers.habr import HabrCareerParser
from job_ftch.infrastructure.sources.site_parsers.rabota import RabotaByParser
from job_ftch.infrastructure.sources.site_parsers.sber import SberParser, _sber_slug
from job_ftch.infrastructure.sources.site_parsers.tbank import (
    TbankCareerParser,
    _extract_detail_urls,
)
from job_ftch.infrastructure.sources.site_parsers.vk import VkTeamParser


@dataclass
class _FakeResponse:
    text: str
    url: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def get(
        self, url: str, *, follow_redirects: bool = True, **kwargs: object
    ) -> _FakeResponse:
        del follow_redirects, kwargs
        return self._responses[url]


@pytest.mark.asyncio
async def test_habr_parser_discovers_detail_urls() -> None:
    parser = HabrCareerParser()
    client = _FakeClient(
        {
            "https://career.habr.com/vacancies": _FakeResponse(
                '<a href="/vacancies/100500">Job</a>',
                "https://career.habr.com/vacancies",
            )
        }
    )
    spec = CareerSiteSpec(url="https://career.habr.com/vacancies", source_name="habr_jobs")

    urls = await parser.discover(spec, client)

    assert urls == ["https://career.habr.com/vacancies/100500"]


@pytest.mark.asyncio
async def test_habr_parser_discovers_detail_urls_across_pages() -> None:
    parser = HabrCareerParser()
    client = _FakeClient(
        {
            "https://career.habr.com/vacancies?q=ml": _FakeResponse(
                '<a href="/vacancies/100500">Job</a>',
                "https://career.habr.com/vacancies?q=ml",
            ),
            "https://career.habr.com/vacancies?q=ml&page=2": _FakeResponse(
                '<a href="/vacancies/100501">Job 2</a>',
                "https://career.habr.com/vacancies?q=ml&page=2",
            ),
        }
    )
    spec = CareerSiteSpec(
        url="https://career.habr.com/vacancies?q=ml",
        source_name="habr_jobs",
        limit=25,
    )

    urls = await parser.discover(spec, client)

    assert urls == [
        "https://career.habr.com/vacancies/100500",
        "https://career.habr.com/vacancies/100501",
    ]


@pytest.mark.asyncio
async def test_geekjob_parser_discovers_static_listing_urls() -> None:
    parser = GeekJobParser()
    client = _FakeClient(
        {
            "https://geekjob.ru/vacancies": _FakeResponse(
                '<a href="/vacancy/senior-ml-engineer"></a><a href="/jobs/648644"></a>',
                "https://geekjob.ru/vacancies",
            )
        }
    )

    urls = await parser.discover(
        CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="geekjob_jobs", limit=2),
        client,
    )

    assert urls == [
        "https://geekjob.ru/vacancy/senior-ml-engineer",
        "https://geekjob.ru/jobs/648644",
    ]


@pytest.mark.asyncio
async def test_geekjob_parser_uses_browser_fallback_for_lazy_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = GeekJobParser()
    client = _FakeClient(
        {
            "https://geekjob.ru/vacancies": _FakeResponse(
                "<html><body><div id='root'></div></body></html>",
                "https://geekjob.ru/vacancies",
            )
        }
    )

    @asynccontextmanager
    async def _fake_open_page(*args: object, **kwargs: object):
        del args, kwargs
        yield SimpleNamespace(url="https://geekjob.ru/vacancies")

    async def _fake_navigate(page: object, url: str, config: dict[str, object]) -> None:
        del page, url, config

    async def _fake_scroll(*args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        return ["https://geekjob.ru/jobs/648644"]

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.geekjob.open_page", _fake_open_page
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.geekjob.navigate", _fake_navigate
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.geekjob.browser_scroll_collect_urls",
        _fake_scroll,
    )

    urls = await parser.discover(
        CareerSiteSpec(url="https://geekjob.ru/vacancies", source_name="geekjob_jobs", limit=1),
        client,
    )

    assert urls == ["https://geekjob.ru/jobs/648644"]


@pytest.mark.asyncio
async def test_sber_parser_uses_rich_http_api_without_browser() -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "vacancies": [
                        {
                            "internalId": 4545651,
                            "requisitionId": "req-1",
                            "publicationId": "pub-1",
                            "title": "Младший трейдер, Global markets",
                            "company": "ПАО Сбербанк",
                            "publicationDate": "2026-07-16T17:44:58.000Z",
                            "introduction": "Команда глобальных рынков",
                            "duties": "Разрабатывать продукты",
                            "requirements": "Опыт от года",
                            "city": "Москва",
                            "salary": {"from": 300000, "to": 450000, "currency": "RUB"},
                        }
                    ]
                }
            }

    class Client:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str, **_: object) -> Response:
            self.urls.append(url)
            return Response()

    client = Client()
    parser = SberParser()
    spec = CareerSiteSpec(url="https://rabota.sber.ru/search/", source_name="sber", limit=50)

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert client.urls == [
        "https://rabota.sber.ru/public/app-candidate-public-api-gateway/"
        "api/v1/publications?skip=0&take=50"
    ]
    assert str(items[0].url).endswith("/mladshiy-treyder-global-markets-4545651/")
    assert items[0].metadata["detail_vacancy_confirmed"] is True
    assert items[0].metadata["locations"] == ["Москва"]
    assert items[0].metadata["base_salary"] == {
        "currency": "RUB",
        "min": 300000,
        "max": 450000,
    }
    assert items[0].created_at is not None
    assert _sber_slug("Python / AI и LLM") == "python-ai-i-llm"


def test_tbank_extract_detail_urls_rejects_listing_like_pages() -> None:
    import re

    detail_re = re.compile(
        r"https?://(?:www\.)?tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/"
        r"(?:[a-z0-9-]+/)+[a-z0-9-]+/?$",
        re.IGNORECASE,
    )
    listing_re = re.compile(
        r"/career/(?:vacancies/(?:all|service|back-office|it)|service/|blog(?:/|$)|technologies(?:/|$))",
        re.IGNORECASE,
    )
    urls = _extract_detail_urls(
        '<a href="https://www.tbank.ru/career/vacancies/all/moscow/">Listing</a>',
        "https://www.tbank.ru/career/vacancies/it/",
        limit=5,
        detail_re=detail_re,
        listing_re=listing_re,
    )

    assert urls == []


@pytest.mark.asyncio
async def test_tbank_parser_discovers_detail_items_from_relative_links() -> None:
    parser = TbankCareerParser()
    client = _FakeClient(
        {
            "https://www.tbank.ru/career/vacancies/it/": _FakeResponse(
                '<a href="/career/it/vacancy/moscow/ml-engineer-123">Job</a>',
                "https://www.tbank.ru/career/vacancies/it/",
            )
        }
    )
    spec = CareerSiteSpec(
        url="https://www.tbank.ru/career/vacancies/it/",
        source_name="tbank_jobs",
        limit=1,
    )

    urls = await parser.discover(spec, client)

    assert urls == ["https://www.tbank.ru/career/it/vacancy/moscow/ml-engineer-123"]


@pytest.mark.asyncio
async def test_tbank_parser_uses_browser_fallback_for_hydrated_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = TbankCareerParser()
    client = _FakeClient(
        {
            "https://www.tbank.ru/career/vacancies/it/": _FakeResponse(
                "<html><body><div id='root'></div></body></html>",
                "https://www.tbank.ru/career/vacancies/it/",
            )
        }
    )

    class _Page:
        url = "https://www.tbank.ru/career/vacancies/it/"

        async def evaluate(self, expression: str) -> list[str]:
            del expression
            return [
                "https://www.tbank.ru/career/it/vacancy/ml-engineer/123e4567-e89b-12d3-a456-426614174000/"
            ]

        async def content(self) -> str:
            return "<html></html>"

    @asynccontextmanager
    async def _fake_open_page(*args: object, **kwargs: object):
        del args, kwargs
        yield _Page()

    async def _fake_navigate(page: object, url: str, config: dict[str, object]) -> None:
        del page, url, config

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.tbank.open_page", _fake_open_page
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.tbank.navigate", _fake_navigate
    )

    spec = CareerSiteSpec(
        url="https://www.tbank.ru/career/vacancies/it/",
        source_name="tbank_jobs",
        monitor_config={"_bypass_strategy": SimpleNamespace(available_tiers=())},
    )

    urls = await parser.discover(spec, client)

    assert urls == [
        "https://www.tbank.ru/career/it/vacancy/ml-engineer/123e4567-e89b-12d3-a456-426614174000/"
    ]


@pytest.mark.asyncio
async def test_tbank_parser_merges_static_and_browser_loaded_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = TbankCareerParser()
    first = "https://www.tbank.ru/career/it/vacancy/moscow/ml-product/first/"
    second = "https://www.tbank.ru/career/it/vacancy/moscow/ai-product/second/"
    client = _FakeClient(
        {
            "https://www.tbank.ru/career/vacancies/it/": _FakeResponse(
                f'<a href="{first}">First</a>',
                "https://www.tbank.ru/career/vacancies/it/",
            )
        }
    )

    @asynccontextmanager
    async def _fake_open_page(*args: object, **kwargs: object):
        del args, kwargs
        yield SimpleNamespace(url="https://www.tbank.ru/career/vacancies/it/")

    async def _fake_navigate(page: object, url: str, config: dict[str, object]) -> None:
        del page, url, config

    async def _fake_scroll(*args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        return [first, second]

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.tbank.open_page", _fake_open_page
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.tbank.navigate", _fake_navigate
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_parsers.tbank.browser_scroll_collect_urls",
        _fake_scroll,
    )

    urls = await parser.discover(
        CareerSiteSpec(
            url="https://www.tbank.ru/career/vacancies/it/",
            source_name="tbank_jobs",
            limit=2,
        ),
        client,
    )

    assert urls == [first, second]


@pytest.mark.asyncio
async def test_vk_parser_uses_public_api_and_preserves_search_query() -> None:
    api_url = (
        "https://team.vk.company/career/api/v2/vacancies/?limit=50&offset=0&title=%D0%98%D0%98"
    )
    client = _FakeClient(
        {
            api_url: _FakeResponse(
                '{"count": 2, "results": [{"id": 52146}, {"id": 51999}]}',
                api_url,
            )
        }
    )

    urls = await VkTeamParser().discover(
        CareerSiteSpec(
            url="https://team.vk.company/vacancy/?query=%D0%98%D0%98",
            source_name="vk_team",
            limit=50,
        ),
        client,
    )

    assert urls == [
        "https://team.vk.company/vacancy/52146/",
        "https://team.vk.company/vacancy/51999/",
    ]


@pytest.mark.asyncio
async def test_rabota_by_parser_delegates_to_hh_family_shape() -> None:
    client = _FakeClient(
        {
            "https://rabota.by/search/vacancy?text=ai": _FakeResponse(
                '<a href="https://rabota.by/vacancy/999?query=ai">Job</a>',
                "https://rabota.by/search/vacancy?text=ai",
            ),
            "https://rabota.by/vacancy/999": _FakeResponse(
                """
                <script type="application/ld+json">
                {"@type": "JobPosting", "title": "AI PM", "description": "<p>Ship AI products</p>"}
                </script>
                """,
                "https://rabota.by/vacancy/999",
            ),
        }
    )
    parser = RabotaByParser()
    spec = CareerSiteSpec(
        url="https://rabota.by/search/vacancy?text=ai",
        source_name="rabota_by",
        limit=1,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "999"


@pytest.mark.asyncio
async def test_rabota_by_parser_normalizes_root_to_search_listing() -> None:
    client = _FakeClient(
        {
            "https://rabota.by/search/vacancy?area=16": _FakeResponse(
                '<a href="https://hh.ru/vacancy/123">Foreign</a><a href="https://rabota.by/vacancy/999">Job</a>',
                "https://rabota.by/search/vacancy?area=16",
            ),
            "https://hh.ru/vacancy/123": _FakeResponse(
                """
                <script type="application/ld+json">
                {"@type": "JobPosting", "title": "Wrong host", "description": "<p>Ignore</p>"}
                </script>
                """,
                "https://hh.ru/vacancy/123",
            ),
            "https://rabota.by/vacancy/999": _FakeResponse(
                """
                <script type="application/ld+json">
                {"@type": "JobPosting", "title": "AI PM", "description": "<p>Ship AI products</p>"}
                </script>
                """,
                "https://rabota.by/vacancy/999",
            ),
        }
    )

    parser = RabotaByParser()
    spec = CareerSiteSpec(url="https://rabota.by/", source_name="rabota_by", limit=1)

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "999"
    assert str(items[0].url).startswith("https://rabota.by/vacancy/")


@pytest.mark.asyncio
async def test_greenhouse_monitor_does_not_guess_token_from_arbitrary_slug() -> None:
    client = _FakeClient(
        {
            "https://rabota.by/": _FakeResponse(
                "<html><body>No greenhouse here</body></html>",
                "https://rabota.by/",
            )
        }
    )

    result = await greenhouse_can_handle("https://rabota.by/", client)

    assert result is None


@pytest.mark.asyncio
async def test_lever_monitor_does_not_guess_token_from_arbitrary_slug() -> None:
    client = _FakeClient(
        {
            "https://az.linkedin.com/jobs": _FakeResponse(
                "<html><body>No lever board here</body></html>",
                "https://az.linkedin.com/jobs",
            )
        }
    )

    result = await lever_can_handle("https://az.linkedin.com/jobs", client)

    assert result is None
