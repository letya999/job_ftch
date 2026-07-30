from __future__ import annotations

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.yandex import (
    YandexJobsParser,
    _extract_ssr_vacancy_urls,
    _item_from_api,
    _item_from_detail_html,
)


class _FakeResponse:
    def __init__(self, text: str, url: str, status_code: int = 200) -> None:
        self.text = text
        self.url = url
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True) -> _FakeResponse:
        del follow_redirects
        return self._responses[url]


def test_extract_ssr_vacancy_urls_skips_city_filters() -> None:
    html = """
    <a href="/jobs/vacancies/data-analyst-123">Vacancy</a>
    <a href="/jobs/vacancies/city_moscow">City</a>
    <a href="/jobs/vacancies/ml-engineer-456">Vacancy 2</a>
    """

    urls = _extract_ssr_vacancy_urls(html, "https://yandex.ru/jobs/vacancies?text=ai", limit=10)

    assert urls == [
        "https://yandex.ru/jobs/vacancies/data-analyst-123",
        "https://yandex.ru/jobs/vacancies/ml-engineer-456",
    ]


def test_item_from_detail_html_uses_visible_main_content() -> None:
    html = """
    <main>
      <h1>ML Engineer</h1>
      <div>От 3 лет</div>
      <div>Удалённая работа</div>
      <p>Развивать модели ранжирования и инфраструктуру качества.</p>
    </main>
    """

    item = _item_from_detail_html(
        "https://yandex.ru/jobs/vacancies/ml-engineer-456",
        html,
        "yandex_jobs_ru",
    )

    assert item is not None
    assert item.external_id == "456"
    assert "ML Engineer" in item.text
    assert "Удалённая работа" in item.text


def test_item_from_api_resolves_slug_against_listing_origin() -> None:
    item = _item_from_api(
        {
            "id": 456,
            "title": "ML Engineer",
            "publication_slug_url": "/jobs/vacancies/ml-engineer-456",
        },
        "https://yandex.ru/jobs/vacancies?text=ai",
        "yandex_jobs_ru",
    )

    assert item is not None
    assert str(item.url) == "https://yandex.ru/jobs/vacancies/ml-engineer-456"


@pytest.mark.asyncio
async def test_yandex_parser_prefers_ssr_listing_path() -> None:
    listing_html = """
    <a href="/jobs/vacancies/data-analyst-123">Vacancy</a>
    <a href="/jobs/vacancies/city_moscow">Filter</a>
    """
    detail_html = """
    <main>
      <h1>Data Analyst</h1>
      <p>Анализировать данные и улучшать ML-продукты.</p>
    </main>
    """
    parser = YandexJobsParser()
    client = _FakeClient(
        {
            "https://yandex.ru/jobs/vacancies?text=ai": _FakeResponse(
                listing_html,
                "https://yandex.ru/jobs/vacancies?text=ai",
            ),
            "https://yandex.ru/jobs/vacancies/data-analyst-123": _FakeResponse(
                detail_html,
                "https://yandex.ru/jobs/vacancies/data-analyst-123",
            ),
        }
    )
    spec = CareerSiteSpec(
        url="https://yandex.ru/jobs/vacancies?text=ai",
        source_name="yandex_jobs_ru",
        limit=1,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "123"
    assert "Data Analyst" in items[0].text


@pytest.mark.asyncio
async def test_yandex_parser_normalizes_jobs_root_to_vacancy_listing() -> None:
    listing_html = '<a href="/jobs/vacancies/data-analyst-123">Vacancy</a>'
    detail_html = "<main><h1>Data Analyst</h1></main>"
    parser = YandexJobsParser()
    client = _FakeClient(
        {
            "https://yandex.ru/jobs/vacancies": _FakeResponse(
                listing_html, "https://yandex.ru/jobs/vacancies"
            ),
            "https://yandex.ru/jobs/vacancies/data-analyst-123": _FakeResponse(
                detail_html, "https://yandex.ru/jobs/vacancies/data-analyst-123"
            ),
        }
    )

    items = [
        item
        async for item in parser.parse(
            CareerSiteSpec(url="https://www.yandex.ru/jobs/", limit=1), client
        )
    ]

    assert len(items) == 1
    assert str(items[0].url) == "https://yandex.ru/jobs/vacancies/data-analyst-123"
