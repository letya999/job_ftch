from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirify import (
    HirifyParser,
    _extract_detail_urls,
)


def test_extract_detail_urls_finds_canonical_hirify_jobs() -> None:
    html = """
    <a href="/jobs/711365-software-engineer-genai-silicon-automation">Role</a>
    <a href="/ai-engineering-jobs">Category</a>
    """

    urls = _extract_detail_urls(html, "https://hirify.me/", limit=5)

    assert urls == ["https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"]


@pytest.mark.asyncio
async def test_hirify_parser_discovers_urls_from_listing() -> None:
    parser = HirifyParser()

    class _Response(SimpleNamespace):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, *, follow_redirects: bool = True) -> _Response:
            del follow_redirects
            return _Response(
                text='<a href="/jobs/711365-software-engineer-genai-silicon-automation">Role</a>',
                url=url,
            )

    spec = CareerSiteSpec(url="https://hirify.me/", source_name="hirify")
    urls = await parser.discover(spec, _Client())

    assert urls == ["https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"]


# ---------------------------------------------------------------------------
# API-backed body extraction.
#
# hirify.me is a Nuxt SPA: the rendered detail page exposes only card chrome and
# tag chips, so scraping it stored a 254-character "description" made of
# "Show contacts", "Report" and a keyword list. The real posting lives behind
# /api/vacancies/{id}. These tests pin that the body comes from the API and that
# the parser stays silent - letting CareerSiteSource fall back to the generic
# crawl - when the API cannot answer.
# ---------------------------------------------------------------------------

_LISTING_ROW = {
    "id": 668118,
    "slug": "668118-product-owner-ai-platform",
    "title": "Product Owner (AI)",
    "company_title": "Hirify",
    "work_format": ["remote"],
    "work_type": "fulltime",
    "vacancy_language": "ru",
    "created_at": "2026-07-30T10:00:00.000000Z",
    "regions": [{"id": 1, "code": "russia", "name": "Россия", "name_en": "Russia"}],
    "grades": [{"id": 3, "name": "middle"}],
    "tags": [{"id": 1, "name": "agile"}, {"id": 2, "name": "jira"}],
    "specializations": [{"id": 9, "name": "Продукт", "name_en": "Product"}],
    "salary": {"currency": "RUB", "min": 250000, "max": 300000},
}

# Shaped like the live response: it repeats the listing row and adds the body.
_DETAIL_BODY = {
    "id": 668118,
    "title": "Product Owner (AI)",
    "company_title": "Hirify",
    "text": (
        "<p><strong>Product Owner (AI)</strong></p>"
        "<p>Product Owner отвечает не за всю платформу, а за конкретный сервис "
        "внутри AI-платформы. Он работает в сервисной команде, берет backlog "
        "своего сервиса и детализирует его до уровня, понятного разработке, "
        "дизайну и тестированию.</p>"
        "<p>Обязанности: вести backlog конкретного сервиса платформы ИИ, "
        "декомпозировать инициативы до задач, писать user story, use case и "
        "acceptance criteria, участвовать в refinement, planning и review.</p>"
        "<p>Требования: опыт от 3 лет в роли Product Owner, системного "
        "аналитика или BA с обязанностями PO, уверенная работа в Jira, "
        "понимание Definition of Ready и Definition of Done.</p>"
    ),
    "tldr": "Управление бэклогом сервиса внутри AI-платформы.",
    # The detail response leaves some lookup lists empty; a plain overlay would
    # wipe the values the listing did carry.
    "grades": [],
    "tags": [],
}


class _ApiClient:
    """Serves the feed page, the listing endpoint and per-vacancy details."""

    def __init__(self, *, listing: object = None, detail: object = None) -> None:
        self._listing = {"data": [_LISTING_ROW]} if listing is None else listing
        self._detail = _DETAIL_BODY if detail is None else detail
        self.detail_calls: list[str] = []

    async def get(self, url: str, **kwargs: object) -> object:
        del kwargs
        if "/api/vacancies/" in url:
            self.detail_calls.append(url)
            return _JsonResponse(self._detail, url)
        if "/api/vacancies" in url:
            if isinstance(self._listing, Exception):
                raise self._listing
            return _JsonResponse(self._listing, url)
        return _JsonResponse(None, url, text="<html>feed</html>")


class _JsonResponse(SimpleNamespace):
    def __init__(self, payload: object, url: str, text: str = "") -> None:
        super().__init__(url=url)
        self.status_code = 200
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _spec() -> CareerSiteSpec:
    return CareerSiteSpec(url="https://hirify.me/jobs-in-russia", source_name="hirify_me", limit=5)


@pytest.mark.asyncio
async def test_body_comes_from_the_per_vacancy_endpoint() -> None:
    client = _ApiClient()

    items = [item async for item in HirifyParser().parse(_spec(), client)]

    assert len(items) == 1
    item = items[0]
    assert "конкретный сервис" in item.text
    assert "acceptance criteria" in item.text
    # The chips-only scrape produced 254 characters; the real posting is longer.
    assert len(item.text) > 254
    assert "<p>" not in item.text, "HTML must be flattened"
    assert client.detail_calls == ["https://api.hirify.me/api/vacancies/668118"]


@pytest.mark.asyncio
async def test_structured_fields_reach_metadata() -> None:
    items = [item async for item in HirifyParser().parse(_spec(), _ApiClient())]

    metadata = items[0].metadata
    assert metadata["adapter"] == "hirify-api"
    assert metadata["detail_vacancy_confirmed"] is True
    assert metadata["company"] == "Hirify"
    assert metadata["locations"] == ["Россия"]
    assert metadata["work_modes"] == ["remote"]
    assert metadata["base_salary"]["min"] == 250000
    assert str(items[0].url) == "https://hirify.me/jobs/668118-product-owner-ai-platform"


@pytest.mark.asyncio
async def test_empty_detail_lists_do_not_wipe_listing_values() -> None:
    """The detail payload returns grades/tags empty; the listing had them."""
    items = [item async for item in HirifyParser().parse(_spec(), _ApiClient())]

    metadata = items[0].metadata
    assert metadata["seniority_hints"] == ["middle"]
    assert metadata["skills"] == ["agile", "jira"]
    assert metadata["specializations"] == ["Продукт"]


@pytest.mark.asyncio
async def test_tldr_used_when_detail_body_is_missing() -> None:
    """A failed detail call must not lose the vacancy outright."""
    client = _ApiClient(detail={"id": 668118, "tldr": "Короткое описание вакансии."})

    items = [item async for item in HirifyParser().parse(_spec(), client)]

    assert len(items) == 1
    assert "Короткое описание вакансии." in items[0].text
    assert items[0].metadata["detail_vacancy_confirmed"] is False


@pytest.mark.asyncio
async def test_yields_nothing_when_listing_api_fails_so_generic_crawl_runs() -> None:
    """CareerSiteSource falls through to the generic crawl on an empty parse."""
    client = _ApiClient(listing=RuntimeError("api down"))

    items = [item async for item in HirifyParser().parse(_spec(), client)]

    assert items == []


@pytest.mark.asyncio
async def test_discover_recovers_ids_when_listing_api_fails() -> None:
    """Discovery still finds detail URLs in the page HTML, and the per-vacancy
    endpoint is separate, so the body is recoverable without the listing."""

    class _Client(_ApiClient):
        async def get(self, url: str, **kwargs: object) -> object:
            if "/api/vacancies/" in url:
                self.detail_calls.append(url)
                return _JsonResponse(_DETAIL_BODY, url)
            if "/api/vacancies" in url:
                raise RuntimeError("listing api down")
            return _JsonResponse(
                None,
                url,
                text='<a href="/jobs/668118-product-owner-ai-platform">Role</a>',
            )

    client = _Client()
    items = [item async for item in HirifyParser().parse(_spec(), client)]

    assert len(items) == 1
    assert "конкретный сервис" in items[0].text
    assert client.detail_calls == ["https://api.hirify.me/api/vacancies/668118"]


@pytest.mark.asyncio
async def test_parser_does_not_suppress_the_generic_fallback() -> None:
    """Setting either flag would stop CareerSiteSource from crawling after an
    empty parse, which is the only remaining path when the API is unreachable."""
    parser = HirifyParser()

    assert getattr(parser, "confirmed_empty_on_empty", False) is False
    assert getattr(parser, "terminal_on_empty", False) is False
