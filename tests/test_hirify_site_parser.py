from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.hirify import (
    HirifyParser,
    HirifyRateLimitedError,
    _extract_detail_urls,
    _HirifyRateGate,
    _listing_card_rows,
    _next_api_request,
    _retry_after_seconds,
)
from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope


def test_extract_detail_urls_finds_canonical_hirify_jobs() -> None:
    html = """
    <a href="/jobs/711365-software-engineer-genai-silicon-automation">Role</a>
    <a href="/ai-engineering-jobs">Category</a>
    """

    urls = _extract_detail_urls(html, "https://hirify.me/", limit=5)

    assert urls == ["https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"]


def test_ssr_cards_keep_ids_and_card_fields_for_api_fallback() -> None:
    html = """
    <div class="vacancy-card" data-vacancy-id="711365">
      <a class="vacancy-card-link" href="/jobs/711365-manager">
        <div class="company">Acme</div>
        <div class="tag">remote</div>
        <h3 class="title">Operations Manager</h3>
      </a>
    </div>
    """

    rows = _listing_card_rows(html, "https://hirify.me/jobs-in-russia", limit=5)

    assert rows == [
        {
            "id": "711365",
            "slug": "711365-manager",
            "title": "Operations Manager",
            "company_title": "Acme",
            "listing_card_tags": ["remote"],
            "listing_card_text": "Acme remote Operations Manager",
        }
    ]


def test_cursor_pagination_becomes_a_request_parameter() -> None:
    request = _next_api_request(
        {"data": [], "meta": {"next_cursor": "cursor-2"}},
        current_url="https://api.hirify.me/api/vacancies",
        api_url="https://api.hirify.me/api/vacancies",
        query={"search": "manager"},
        page=1,
    )

    assert request == (
        "https://api.hirify.me/api/vacancies",
        {"search": "manager", "cursor": "cursor-2"},
        1,
    )


def test_retry_after_prefers_header_then_parses_hirify_body() -> None:
    assert _retry_after_seconds({"Retry-After": "17"}, "wait 3 seconds") == 17.0
    assert _retry_after_seconds({}, '{"retry_after": 11}') == 11.0
    assert _retry_after_seconds({}, "Please try again in 9 seconds") == 9.0


@pytest.mark.asyncio
async def test_api_rate_gate_uses_raw_client_under_retrying_wrapper() -> None:
    class _RawClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, url: str, **kwargs: object) -> _JsonResponse:
            del kwargs
            self.calls += 1
            return _JsonResponse({"data": []}, url)

    class _RetryingWrapper:
        def __init__(self, raw: _RawClient) -> None:
            self._client = raw

        async def get(self, url: str, **kwargs: object) -> _JsonResponse:
            del url, kwargs
            raise AssertionError("wrapper should not handle Hirify 429s")

    raw = _RawClient()
    response = await HirifyParser()._api_get(
        _RetryingWrapper(raw),
        "https://api.hirify.me/api/vacancies",
        headers={},
    )

    assert response.status_code == 200
    assert raw.calls == 1


@pytest.mark.asyncio
async def test_detail_rate_limit_does_not_sleep_retry_after() -> None:
    class _RawClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, url: str, **kwargs: object) -> _JsonResponse:
            del kwargs
            self.calls += 1
            response = _JsonResponse({}, url, text='{"retry_after": 38}')
            response.status_code = 429
            response.headers = {"Retry-After": "38"}
            return response

    started = asyncio.get_running_loop().time()
    with pytest.raises(HirifyRateLimitedError):
        await HirifyParser()._api_get(
            _RawClient(),
            "https://api.hirify.me/api/vacancies/668118",
            headers={},
            retry_rate_limit=False,
        )
    assert asyncio.get_running_loop().time() - started < 1.0


@pytest.mark.asyncio
async def test_rate_gate_does_not_sleep_past_source_budget() -> None:
    gate = _HirifyRateGate()
    gate.record_rate_limit(120)
    loop = asyncio.get_running_loop()

    async with source_deadline_scope(loop.time() + 0.01):
        with pytest.raises(HirifyRateLimitedError, match="exceeds remaining source budget"):
            await gate.acquire()


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
async def test_listing_api_paginates_until_limit() -> None:
    class Client(_ApiClient):
        def __init__(self) -> None:
            super().__init__()
            self.pages: list[str] = []

        async def get(self, url: str, **kwargs: object) -> object:
            if url.endswith("/api/vacancies"):
                page = str(kwargs.get("params", {}).get("page"))  # type: ignore[union-attr]
                self.pages.append(page)
                row = {**_LISTING_ROW, "id": 668117 + int(page), "slug": f"role-{page}"}
                return _JsonResponse(
                    {"data": [row], "next_page_url": "next" if page == "1" else None}, url
                )
            return await super().get(url, **kwargs)

    client = Client()
    spec = _spec().model_copy(update={"limit": 2})

    items = [item async for item in HirifyParser().parse(spec, client)]

    assert client.pages == ["1", "2"]
    assert len(items) == 2


@pytest.mark.asyncio
async def test_one_rate_limited_detail_does_not_discard_successful_details() -> None:
    second_row = {**_LISTING_ROW, "id": 668119, "slug": "668119-manager"}
    client = _ApiClient(listing={"data": [_LISTING_ROW, second_row]})
    parser = HirifyParser()

    async def _detail(
        client: object,
        html: str,
        vacancy_id: object,
        referer: str,
    ) -> dict[str, object] | None:
        del client, html, referer
        if str(vacancy_id) == "668119":
            raise HirifyRateLimitedError("429")
        return _DETAIL_BODY

    parser._fetch_detail_body = _detail  # type: ignore[method-assign]

    browser_calls = {"count": 0}

    async def _browser_details(
        spec: CareerSiteSpec,
        html: str,
        rows: list[dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        del spec, html, rows
        browser_calls["count"] += 1
        return {}

    parser._fetch_details_via_browser = _browser_details  # type: ignore[method-assign]
    items = [item async for item in parser.parse(_spec(), client)]

    assert browser_calls["count"] == 0
    assert [item.external_id for item in items] == ["668118", "668119"]
    assert items[0].metadata["detail_vacancy_confirmed"] is True
    assert items[1].metadata["detail_vacancy_confirmed"] is False
    assert "Product Owner (AI)" in items[1].text


@pytest.mark.asyncio
async def test_rate_limited_listing_expands_from_browser_discovery() -> None:
    parser = HirifyParser()
    discovered = [
        "https://hirify.me/jobs/668118-product-owner-ai-platform",
        "https://hirify.me/jobs/668119-manager",
    ]

    async def _rate_limited_listing(
        spec: CareerSiteSpec,
        client: object,
        html: str,
    ) -> list[dict[str, object]]:
        del spec, client, html
        raise HirifyRateLimitedError("429", retry_after_seconds=2)

    async def _discover(spec: CareerSiteSpec, client: object) -> list[str]:
        del spec, client
        return discovered

    async def _detail(
        client: object,
        html: str,
        vacancy_id: object,
        referer: str,
    ) -> dict[str, object]:
        del client, html, referer
        return {**_DETAIL_BODY, "id": vacancy_id}

    class _UnfilteredSsrClient(_ApiClient):
        async def get(self, url: str, **kwargs: object) -> object:
            if "/api/vacancies" not in url:
                return _JsonResponse(
                    None,
                    url,
                    text=(
                        '<div class="vacancy-card" data-vacancy-id="668117">'
                        '<a class="vacancy-card-link" href="/jobs/668117-unrelated">'
                        '<h3 class="title">Unrelated role</h3></a></div>'
                    ),
                )
            return await super().get(url, **kwargs)

    parser._fetch_listing_rows = _rate_limited_listing  # type: ignore[method-assign]
    parser.discover = _discover  # type: ignore[assignment]
    parser._fetch_detail_body = _detail  # type: ignore[method-assign]
    spec = _spec().model_copy(
        update={"limit": 2, "url": "https://hirify.me/jobs-in-russia?search=product+owner"}
    )

    items = [item async for item in parser.parse(spec, _UnfilteredSsrClient())]

    assert [item.external_id for item in items] == ["668118", "668119"]


@pytest.mark.asyncio
async def test_search_keeps_project_manager_titles_not_any_manager() -> None:
    sales = {**_LISTING_ROW, "id": 1, "slug": "1-sales", "title": "Head of Sales (SaaS)"}
    project = {
        **_LISTING_ROW,
        "id": 2,
        "slug": "2-project-manager",
        "title": "IT Project Manager",
    }
    product = {
        **_LISTING_ROW,
        "id": 3,
        "slug": "3-product-manager",
        "title": "Staff Product Manager",
    }
    client = _ApiClient(listing={"data": [sales, project, product]})

    async def _detail(
        client: object,
        html: str,
        vacancy_id: object,
        referer: str,
    ) -> dict[str, object] | None:
        del client, html, referer
        return {"id": vacancy_id, "tldr": "listing fallback"}

    parser = HirifyParser()
    parser._fetch_detail_body = _detail  # type: ignore[method-assign]
    spec = _spec().model_copy(
        update={"url": "https://hirify.me/jobs-in-russia?search=project+manager", "limit": 10}
    )
    items = [item async for item in parser.parse(spec, client)]

    assert [item.external_id for item in items] == ["2"]
    assert "IT Project Manager" in items[0].text


@pytest.mark.asyncio
async def test_search_api_still_runs_when_listing_page_is_unavailable() -> None:
    class _ApiOnlyClient(_ApiClient):
        async def get(self, url: str, **kwargs: object) -> object:
            if "/api/vacancies" in url:
                return await super().get(url, **kwargs)
            raise RuntimeError("listing page unavailable")

    items = [item async for item in HirifyParser().parse(_spec(), _ApiOnlyClient())]

    assert len(items) == 1


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

    parser = HirifyParser()

    # When both the listing API and discovery fail, parse() must yield nothing
    # so that CareerSiteSource falls through to the generic crawl. The mock
    # client cannot prevent the browser path inside discover(), so we stub
    # discover() itself.
    async def _no_discover(spec: object, client: object) -> list[str]:
        raise RuntimeError("api down")

    parser.discover = _no_discover  # type: ignore[assignment]
    items = [item async for item in parser.parse(_spec(), client)]

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
