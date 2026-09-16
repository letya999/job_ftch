from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hh import (
    HhParser,
    _detail_identity,
    _extract_next_listing_url,
    _extract_vacancy_urls,
    _item_from_detail_html,
    _listing_page_url,
    _normalize_listing_url,
)


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

    async def get(self, url: str, *, follow_redirects: bool = True) -> _FakeResponse:
        del follow_redirects
        return self._responses[url]


def test_hh_runtime_defaults_authorize_proxy_and_captcha_hosts() -> None:
    from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults

    spec = apply_runtime_defaults(CareerSiteSpec(url="https://hh.ru/search/vacancy"))
    assert "hh.ru" in spec.monitor_config["proxy_rescue_allow_domains"]
    assert "hh.kz" in spec.monitor_config["captcha_authorized_domains"]


def test_listing_page_url_adds_page_query() -> None:
    assert _listing_page_url("https://hh.ru/search/vacancy?text=ai", 0) == (
        "https://hh.ru/search/vacancy?text=ai"
    )


def test_normalize_hh1_root_to_vacancy_listing() -> None:
    assert _normalize_listing_url("https://hh1.az/") == "https://hh1.az/search/vacancy"
    assert _normalize_listing_url("https://hh.ru/") == "https://hh.ru/search/vacancy"
    assert _normalize_listing_url("https://hh.kz/search") == "https://hh.kz/search/vacancy"
    assert (
        _normalize_listing_url("https://hh1.az/search/vacancy") == "https://hh1.az/search/vacancy"
    )
    assert _listing_page_url("https://hh.ru/search/vacancy?text=ai", 2) == (
        "https://hh.ru/search/vacancy?text=ai&page=2"
    )


def test_explicit_limit_scales_beyond_production_page_budget() -> None:
    from types import SimpleNamespace

    parser = HhParser()
    parser._manifest_entry = SimpleNamespace(extra={"max_listing_pages": 5, "listing_page_size": 50})
    assert parser._max_listing_pages() == 5
    assert parser._max_listing_pages(500) == 10
    assert parser._max_listing_pages(535) == 11
    assert parser._max_listing_pages(100000) == 50


@pytest.mark.asyncio
async def test_discovery_adapts_to_actual_page_size() -> None:
    from types import SimpleNamespace

    base = "https://hh.ru/search/vacancy?text=ai"
    responses = {}
    for index in range(6):
        listing = base if index == 0 else f"{base}&page={index}"
        detail = f"https://hh.ru/vacancy/{index + 1}"
        next_link = f'<a rel="next" href="{base}&page={index + 1}">Next</a>' if index < 5 else ""
        alias = detail.replace("hh.ru", "spb.hh.ru")
        responses[listing] = _FakeResponse(
            f'<a href="{detail}">AI Engineer</a><a href="{alias}">Same vacancy</a>{next_link}', listing
        )
        responses[detail] = _FakeResponse(
            '<script type="application/ld+json">{"@type":"JobPosting",'
            '"title":"AI Engineer","description":"Build models"}</script>', detail,
        )
    parser = HhParser()
    parser._manifest_entry = SimpleNamespace(extra={"max_listing_pages": 1, "listing_page_size": 100})
    items = [item async for item in parser.parse(
        CareerSiteSpec(url=base, source_name="hh", limit=6, detail_limit=6), _FakeClient(responses)
    )]
    assert len(items) == 6


def test_normalize_employer_page_to_public_vacancy_listing() -> None:
    assert _normalize_listing_url("https://almaty.hh.kz/employer/1546258") == (
        "https://almaty.hh.kz/search/vacancy?employer_id=1546258"
    )


def test_extract_vacancy_urls_deduplicates_and_normalizes() -> None:
    html = """
    <a href="https://hh.ru/vacancy/123?query=ai&amp;hhtmFrom=vacancy_search_list">one</a>
    <a href="https://hh.ru/vacancy/123?query=ai">dup</a>
    <a href="https://almaty.hh.kz/vacancy/456?hhtmFrom=vacancy_search_list">two</a>
    """

    urls = _extract_vacancy_urls(html, "https://hh.ru/search/vacancy?text=ai", limit=10)

    assert urls == [
        "https://hh.ru/vacancy/123",
        "https://almaty.hh.kz/vacancy/456",
    ]


def test_extract_vacancy_urls_supports_headhunter_kg() -> None:
    html = '<a href="https://headhunter.kg/vacancy/777?hhtmFrom=vacancy_search_list">one</a>'

    urls = _extract_vacancy_urls(html, "https://headhunter.kg/search/vacancy?text=ai", limit=10)

    assert urls == ["https://headhunter.kg/vacancy/777"]


def test_extract_next_listing_url_prefers_explicit_next_link() -> None:
    html = '<a rel="next" href="/search/vacancy?text=ai&page=3">next</a>'

    assert _extract_next_listing_url(html, "https://hh.ru/search/vacancy?text=ai") == (
        "https://hh.ru/search/vacancy?text=ai&page=3"
    )


def test_extract_next_listing_url_finds_next_page_link() -> None:
    html = (
        '<a href="/search/vacancy?text=ai&page=1">1</a>'
        '<a href="/search/vacancy?text=ai&page=2">2</a>'
    )

    assert _extract_next_listing_url(html, "https://hh.ru/search/vacancy?text=ai") == (
        "https://hh.ru/search/vacancy?text=ai&page=1"
    )


def test_detail_identity_collapses_regional_hh_aliases() -> None:
    assert _detail_identity("https://hh.ru/vacancy/123") == _detail_identity(
        "https://spb.hh.ru/vacancy/123"
    )


def test_item_from_detail_html_parses_jobposting_jsonld() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org/",
      "@type": "JobPosting",
      "title": "ML Engineer",
      "description": "<p>Build ranking models</p>",
      "datePosted": "2026-07-03",
      "employmentType": "FULL_TIME",
      "hiringOrganization": {"name": "ACME AI"},
      "jobLocation": [{"address": {"addressLocality": "Moscow", "addressCountry": "RU"}}]
    }
    </script>
    """

    item = _item_from_detail_html(
        "https://hh.ru/vacancy/123456",
        html,
        "hh_ru_jobs",
        "https://hh.ru/search/vacancy?text=ai",
    )

    assert item is not None
    assert item.source_name == "hh_ru_jobs"
    assert item.external_id == "123456"
    assert str(item.url) == "https://hh.ru/vacancy/123456"
    assert "ML Engineer" in item.text
    assert "ACME AI" in item.text
    assert item.metadata["parser"] == "site_hh_jobs"


def test_item_from_detail_html_falls_back_to_hh_dom() -> None:
    html = """
    <h1 data-qa="vacancy-title">AI Engineer</h1>
    <a data-qa="vacancy-company-name">ACME AI</a>
    <div data-qa="vacancy-description"><p>Build reliable agents</p></div>
    """

    item = _item_from_detail_html(
        "https://hh.ru/vacancy/123456",
        html,
        "hh_ru_jobs",
        "https://hh.ru/search/vacancy?text=ai",
    )

    assert item is not None
    assert item.metadata["parser"] == "site_hh_dom"
    assert "Build reliable agents" in item.text


@pytest.mark.asyncio
async def test_hh_parser_emits_items_from_listing_and_detail_pages() -> None:
    listing_html = """
    <a href="https://hh.ru/vacancy/123?query=ai&amp;hhtmFrom=vacancy_search_list">Job 1</a>
    <a href="https://hh.ru/vacancy/456?query=ai&amp;hhtmFrom=vacancy_search_list">Job 2</a>
    """
    detail_html = """
    <script type="application/ld+json">
    {
      "@type": "JobPosting",
      "title": "Data Scientist",
      "description": "<p>Train models</p>",
      "datePosted": "2026-07-03",
      "hiringOrganization": {"name": "Example Co"}
    }
    </script>
    """
    client = _FakeClient(
        {
            "https://hh.ru/search/vacancy?text=ai": _FakeResponse(
                listing_html,
                "https://hh.ru/search/vacancy?text=ai",
            ),
            "https://hh.ru/vacancy/123": _FakeResponse(
                detail_html,
                "https://hh.ru/vacancy/123",
            ),
            "https://hh.ru/vacancy/456": _FakeResponse(
                detail_html.replace("Data Scientist", "ML Engineer"),
                "https://hh.ru/vacancy/456",
            ),
        }
    )
    parser = HhParser()
    spec = CareerSiteSpec(
        url="https://hh.ru/search/vacancy?text=ai",
        source_name="hh_ru_jobs",
        limit=5,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 2
    assert items[0].source_name == "hh_ru_jobs"
    assert "Data Scientist" in items[0].text
    assert "ML Engineer" in items[1].text
    resumed = [
        item async for item in parser.parse(
            spec.model_copy(update={"monitor_config": {"_skip_detail_ids": ["123"]}}), client
        )
    ]
    assert [item.external_id for item in resumed] == ["456"]


@pytest.mark.asyncio
async def test_hh_parser_merges_browser_discovery_after_partial_http_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing_url = "https://hh.ru/search/vacancy?text=ai"
    first_url = "https://hh.ru/vacancy/123"
    second_url = "https://hh.ru/vacancy/456"
    detail = '<script type="application/ld+json">{"@type":"JobPosting","title":"AI Engineer","description":"Build models"}</script>'
    client = _FakeClient(
        {
            listing_url: _FakeResponse(f'<a href="{first_url}">First</a>', listing_url),
            first_url: _FakeResponse(detail, first_url),
            second_url: _FakeResponse(detail.replace("123", "456"), second_url),
        }
    )

    class _Bypass:
        async def apply_http(self, value: object) -> object:
            return value

    async def _discover(*args: object, **kwargs: object) -> tuple[list[str], dict[str, tuple[str, object]]]:
        del args, kwargs
        return [second_url], {"456": ("Second", None)}

    parser = HhParser()
    monkeypatch.setattr(parser, "_discover_with_browser", _discover)
    items = [
        item
        async for item in parser.parse(
            CareerSiteSpec(
                url=listing_url,
                source_name="hh",
                limit=2,
                monitor_config={"_bypass_strategy": _Bypass()},
            ),
            client,
        )
    ]

    assert [item.external_id for item in items] == ["123", "456"]


@pytest.mark.asyncio
async def test_hh_parser_routes_detail_captcha_to_bypass() -> None:
    listing_url = "https://hh.ru/search/vacancy?employer_id=80"
    detail_url = "https://hh.ru/vacancy/123"
    second_detail_url = "https://hh.ru/vacancy/456"
    client = _FakeClient(
        {
            listing_url: _FakeResponse(
                '<div id="123" data-state="{&quot;publicationTime&quot;:'
                '{&quot;@timestamp&quot;:1787558822}}">'
                f'<a data-qa="serp-item__title" href="{detail_url}">Python developer</a>'
                "</div>"
                '<div id="456" data-state="{&quot;publicationTime&quot;:'
                '{&quot;@timestamp&quot;:1787558822}}">'
                f'<a data-qa="serp-item__title" href="{second_detail_url}">ML engineer</a>'
                "</div>",
                listing_url,
            ),
            detail_url: _FakeResponse('<div class="g-recaptcha"></div>', detail_url),
            second_detail_url: _FakeResponse(
                '<script type="application/ld+json">{"@type":"JobPosting",'
                '"title":"ML engineer","description":"Build models"}</script>',
                second_detail_url,
            ),
        }
    )

    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    items = []
    with pytest.raises(BrowserChallengeError):
        async for item in HhParser().parse(
            CareerSiteSpec(url="https://hh.ru/employer/80", source_name="alfa", limit=2),
            client,
        ):
            items.append(item)
    assert [item.external_id for item in items] == ["456"]


@pytest.mark.asyncio
async def test_hh_parser_respects_configured_detail_limit() -> None:
    listing_url = "https://hh.ru/search/vacancy?text=ai"
    first_url = "https://hh.ru/vacancy/1"
    second_url = "https://hh.ru/vacancy/2"
    listing = (
        f'<div id="1"><a href="{first_url}">First</a></div>'
        f'<div id="2"><a href="{second_url}">Second</a></div>'
    )
    detail = '<script type="application/ld+json">{"@type":"JobPosting","title":"First"}</script>'
    client = _FakeClient(
        {
            listing_url: _FakeResponse(listing, listing_url),
            first_url: _FakeResponse(detail, first_url),
        }
    )

    items = [
        item
        async for item in HhParser().parse(
            CareerSiteSpec(url=listing_url, source_name="hh", limit=2, detail_limit=1), client
        )
    ]

    assert [item.text for item in items] == ["First", "Second"]


@pytest.mark.asyncio
async def test_hh_parser_handles_hh_by_redirecting_to_rabota_by_detail() -> None:
    listing_html = '<a href="https://rabota.by/vacancy/999?query=ai">Job</a>'
    detail_html = """
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "AI PM", "description": "<p>Ship AI products</p>"}
    </script>
    """
    client = _FakeClient(
        {
            "https://hh.by/search/vacancy?text=ai": _FakeResponse(
                listing_html,
                "https://rabota.by/search/vacancy?text=ai",
            ),
            "https://rabota.by/vacancy/999": _FakeResponse(
                detail_html,
                "https://rabota.by/vacancy/999",
            ),
        }
    )
    parser = HhParser()
    spec = CareerSiteSpec(
        url="https://hh.by/search/vacancy?text=ai",
        source_name="hh_by_jobs",
        limit=5,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "999"
    assert "AI PM" in items[0].text


@pytest.mark.asyncio
async def test_hh_parser_supports_headhunter_kg_domain() -> None:
    listing_html = '<a href="https://headhunter.kg/vacancy/777?query=ai">Job</a>'
    detail_html = """
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "AI Engineer", "description": "<p>Build agents</p>"}
    </script>
    """
    client = _FakeClient(
        {
            "https://headhunter.kg/search/vacancy?text=ai": _FakeResponse(
                listing_html,
                "https://headhunter.kg/search/vacancy?text=ai",
            ),
            "https://headhunter.kg/vacancy/777": _FakeResponse(
                detail_html,
                "https://headhunter.kg/vacancy/777",
            ),
        }
    )
    parser = HhParser()
    spec = CareerSiteSpec(
        url="https://headhunter.kg/search/vacancy?text=ai",
        source_name="headhunter_kg_jobs",
        limit=5,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "777"
    assert "AI Engineer" in items[0].text
