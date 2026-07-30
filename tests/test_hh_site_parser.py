from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hh import (
    HhParser,
    _detail_identity,
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


def test_listing_page_url_adds_page_query() -> None:
    assert _listing_page_url("https://hh.ru/search/vacancy?text=ai", 0) == (
        "https://hh.ru/search/vacancy?text=ai"
    )


def test_normalize_hh1_root_to_vacancy_listing() -> None:
    assert _normalize_listing_url("https://hh1.az/") == "https://hh1.az/search/vacancy"
    assert (
        _normalize_listing_url("https://hh1.az/search/vacancy") == "https://hh1.az/search/vacancy"
    )
    assert _listing_page_url("https://hh.ru/search/vacancy?text=ai", 2) == (
        "https://hh.ru/search/vacancy?text=ai&page=2"
    )


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
