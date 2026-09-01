from __future__ import annotations

from pathlib import Path

import pytest

from job_ftch.application.public_source_registry import (
    extract_public_failure_code,
    sanitize_source_listing,
)
from job_ftch.application.registry import resolve_site_parser
from job_ftch.domain import SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError
from job_ftch.infrastructure.sources.site_parsers.getmatch import (
    GetmatchIngestError,
    GetmatchPageKind,
    GetmatchParser,
    canonicalize_vacancy_url,
    classify_getmatch_payload,
    extract_vacancy_urls_from_html,
    extract_vacancy_urls_from_sitemap,
    item_from_detail_html,
    public_failure_code_for,
    vacancy_id_from_url,
)


def _fixture(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "real_world" / Path(*parts)


def _read(*parts: str) -> str:
    return _fixture(*parts).read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(
        self,
        text: str,
        url: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get(
        self, url: str, *, follow_redirects: bool = True, **kwargs: object
    ) -> _FakeResponse:
        del follow_redirects, kwargs
        self.calls.append(url)
        if url not in self._responses:
            raise RuntimeError(f"unexpected url: {url}")
        return self._responses[url]


def test_registry_resolves_getmatch_parser() -> None:
    parser = resolve_site_parser("https://getmatch.ru/vacancies?query=ai")
    assert parser is not None
    assert type(parser).__name__ == "GetmatchParser"
    assert parser.has_custom_parse is True


def test_canonical_url_and_dedup_identity_are_stable() -> None:
    a = canonicalize_vacancy_url(
        "https://www.getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty?s=list&utm=1"
    )
    b = canonicalize_vacancy_url("/vacancies/35178-senior-ai-engineer-ai-agenty#frag")
    c = canonicalize_vacancy_url("https://getmatch.ru/vacancies/35178")

    assert a == "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty"
    assert b == a
    assert vacancy_id_from_url(a) == vacancy_id_from_url(c) == "35178"


def test_extract_listing_html_deduplicates_and_ignores_noise() -> None:
    html = _read("site_parsers", "getmatch", "listing.html")
    urls = extract_vacancy_urls_from_html(html, "https://getmatch.ru/vacancies", limit=10)

    assert urls == [
        "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty",
        "https://getmatch.ru/vacancies/34825-ios-developer",
    ]


def test_extract_sitemap_orders_by_id_and_filters_keywords() -> None:
    xml = _read("site_parsers", "getmatch", "sitemap.xml")
    urls = extract_vacancy_urls_from_sitemap(xml, limit=10)
    assert urls[0].endswith("35178-senior-ai-engineer-ai-agenty")
    assert len(urls) == 2

    filtered = extract_vacancy_urls_from_sitemap(xml, limit=10, keywords=["ai engineer"])
    assert filtered == ["https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty"]


def test_getmatch_preserves_phrase_queries_when_reading_url() -> None:
    from job_ftch.infrastructure.sources.site_parsers.getmatch import _keywords_from_spec

    spec = CareerSiteSpec(
        url="https://getmatch.ru/vacancies?query=AI+engineer+OR+LLM+engineer",
        source_name="getmatch",
    )
    assert _keywords_from_spec(spec) == ["AI engineer", "LLM engineer"]


def test_item_from_detail_html_extracts_core_fields() -> None:
    html = _read("site_parsers", "getmatch", "detail.html")
    item = item_from_detail_html(
        "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty?s=x",
        html,
        "getmatch",
        "https://getmatch.ru/vacancies",
    )

    assert item is not None
    assert item.source_kind is SourceKind.CAREER_SITE
    assert item.external_id == "35178"
    assert str(item.url) == "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty"
    assert "Senior AI Engineer" in item.text
    assert "Acme AI" in item.text
    assert "LLM agents" in item.text
    assert item.metadata["parser"] == "site_getmatch"
    assert item.metadata["company"] == "Acme AI"
    assert item.metadata["locations"] == ["Worldwide Remote"]
    assert item.metadata["work_modes"] == ["remote"]
    assert item.metadata["base_salary_text"] is not None
    assert "300 000" in item.metadata["base_salary_text"]
    assert item.metadata["apply_url"].endswith("/vacancies/35178-senior-ai-engineer-ai-agenty")
    assert item.metadata["detail_vacancy_confirmed"] is True
    assert item.created_at is not None
    assert item.created_at.year == 2026


def test_classify_empty_challenge_auth_layout() -> None:
    assert (
        classify_getmatch_payload(_read("site_parsers", "getmatch", "listing_empty.html"))
        is GetmatchPageKind.EMPTY
    )
    assert (
        classify_getmatch_payload(_read("site_parsers", "getmatch", "challenge.html"))
        is GetmatchPageKind.CHALLENGE
    )
    assert (
        classify_getmatch_payload(
            _read("site_parsers", "getmatch", "auth_wall.json"),
            content_type="application/json",
            status_code=401,
        )
        is GetmatchPageKind.AUTH_WALL
    )
    assert (
        classify_getmatch_payload(
            _read("site_parsers", "getmatch", "listing_layout_changed.html"),
            expected="listing",
        )
        is GetmatchPageKind.LAYOUT_CHANGED
    )


def test_embedded_captcha_script_on_real_detail_is_not_challenge() -> None:
    html = _read("site_parsers", "getmatch", "detail.html")
    html = html.replace(
        "</body>",
        '<script src="https://captcha-api.yandex.ru/captcha.js?render=onload"></script></body>',
    )
    assert classify_getmatch_payload(html, expected="detail") is GetmatchPageKind.DETAIL


def test_listing_translation_empty_text_is_not_empty_state() -> None:
    html = """
    <html><body><script id="__NEXT_DATA__" type="application/json">
    {"pageTitle":"Вакансии","seoFilters":{},"list":{"empty":"Вакансий не найдено"}}
    </script></body></html>
    """
    assert classify_getmatch_payload(html, expected="listing") is GetmatchPageKind.LISTING


def test_public_failure_code_for_maps_terminal_kinds() -> None:
    """Typed helper: only terminal Getmatch kinds become public diagnostics codes."""
    assert public_failure_code_for(GetmatchPageKind.EMPTY) == "empty_result"
    assert public_failure_code_for(GetmatchPageKind.LAYOUT_CHANGED) == "layout_changed"
    assert public_failure_code_for(GetmatchPageKind.CHALLENGE) == "challenge_required"
    assert public_failure_code_for(GetmatchPageKind.AUTH_WALL) == "auth_wall"
    assert public_failure_code_for(GetmatchPageKind.LISTING) is None
    assert public_failure_code_for(GetmatchPageKind.DETAIL) is None
    err = GetmatchIngestError("parser_error", "sitemap fetch failed", url="https://getmatch.ru")
    assert public_failure_code_for(err) == "parser_error"
    # Exception string form is what SourceFetchResult.error / last_error carry.
    assert extract_public_failure_code(str(err)) == "parser_error"


@pytest.mark.asyncio
async def test_spa_listing_with_jobs_and_empty_translation_is_not_empty() -> None:
    """Regression: SPA template 'Вакансий не найдено' must not zero-yield when cards exist."""
    listing = """
    <html><head><title>Вакансии — getmatch</title></head>
    <body>
      <h1>Вакансии</h1>
      <div class="b-filter">Изменить фильтры</div>
      <script id="__NEXT_DATA__" type="application/json">
      {"pageTitle":"Вакансии","seoFilters":{},"list":{"empty":"Вакансий не найдено"}}
      </script>
      <a href="/vacancies/35178-senior-ai-engineer-ai-agenty">Senior AI Engineer</a>
    </body></html>
    """
    detail = _read("site_parsers", "getmatch", "detail.html")
    assert classify_getmatch_payload(listing, expected="listing") is GetmatchPageKind.LISTING
    assert public_failure_code_for(classify_getmatch_payload(listing, expected="listing")) is None

    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                listing, "https://getmatch.ru/vacancies"
            ),
            "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty": _FakeResponse(
                detail,
                "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty",
            ),
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch", limit=3)
    items = [item async for item in GetmatchParser().parse(spec, client)]
    assert len(items) == 1
    assert items[0].external_id == "35178"


@pytest.mark.asyncio
async def test_parser_emits_items_from_listing_and_detail() -> None:
    listing = _read("site_parsers", "getmatch", "listing.html")
    detail = _read("site_parsers", "getmatch", "detail.html")
    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                listing, "https://getmatch.ru/vacancies"
            ),
            "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty": _FakeResponse(
                detail,
                "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty",
            ),
            "https://getmatch.ru/vacancies/34825-ios-developer": _FakeResponse(
                detail.replace("Senior AI Engineer", "iOS Developer").replace(
                    "35178-senior-ai-engineer-ai-agenty", "34825-ios-developer"
                ),
                "https://getmatch.ru/vacancies/34825-ios-developer",
            ),
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch", limit=5)

    items = [item async for item in GetmatchParser().parse(spec, client)]

    assert len(items) == 2
    assert {item.external_id for item in items} == {"35178", "34825"}
    assert all(item.metadata["parser"] == "site_getmatch" for item in items)


@pytest.mark.asyncio
async def test_parser_uses_sitemap_when_listing_has_no_cards() -> None:
    spa_shell = """
    <html><head><title>Работа и свежие вакансии в IT — getmatch</title></head>
    <body><h1>Вакансии</h1><div class="b-filter">Изменить фильтры</div></body></html>
    """
    detail = _read("site_parsers", "getmatch", "detail.html")
    sitemap = _read("site_parsers", "getmatch", "sitemap.xml")
    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                spa_shell, "https://getmatch.ru/vacancies"
            ),
            "https://getmatch.ru/sitemap.xml": _FakeResponse(
                sitemap,
                "https://getmatch.ru/sitemap.xml",
                headers={"content-type": "application/xml"},
            ),
            "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty": _FakeResponse(
                detail,
                "https://getmatch.ru/vacancies/35178-senior-ai-engineer-ai-agenty",
            ),
            "https://getmatch.ru/vacancies/34825-ios-developer": _FakeResponse(
                detail.replace("Senior AI Engineer", "iOS Developer"),
                "https://getmatch.ru/vacancies/34825-ios-developer",
            ),
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch", limit=2)

    items = [item async for item in GetmatchParser().parse(spec, client)]

    assert len(items) == 2
    assert "https://getmatch.ru/sitemap.xml" in client.calls


@pytest.mark.asyncio
async def test_empty_listing_is_not_parser_failure() -> None:
    """Real empty UI is empty_result semantics, not parser_error."""
    empty_html = _read("site_parsers", "getmatch", "listing_empty.html")
    kind = classify_getmatch_payload(empty_html, expected="listing")
    assert kind is GetmatchPageKind.EMPTY
    assert public_failure_code_for(kind) == "empty_result"

    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                empty_html,
                "https://getmatch.ru/vacancies",
            )
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch")

    items = [item async for item in GetmatchParser().parse(spec, client)]

    # Empty yield is success path (confirmed_empty_on_empty); not a raised parser_error.
    assert items == []


@pytest.mark.asyncio
async def test_layout_changed_is_degraded_not_successful_zero_yield() -> None:
    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                _read("site_parsers", "getmatch", "listing_layout_changed.html"),
                "https://getmatch.ru/vacancies",
            ),
            "https://getmatch.ru/sitemap.xml": _FakeResponse(
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                "https://getmatch.ru/sitemap.xml",
                headers={"content-type": "application/xml"},
            ),
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch")

    with pytest.raises(GetmatchIngestError) as exc_info:
        _ = [item async for item in GetmatchParser().parse(spec, client)]

    assert exc_info.value.kind == "layout_changed"
    assert public_failure_code_for(exc_info.value) == "layout_changed"
    # Composite stores ``ClassName: layout_changed: …``; public extractor keeps the code.
    composite_error = f"{type(exc_info.value).__name__}: {exc_info.value}"
    assert extract_public_failure_code(composite_error) == "layout_changed"


@pytest.mark.asyncio
async def test_challenge_raises_browser_challenge_error() -> None:
    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                _read("site_parsers", "getmatch", "challenge.html"),
                "https://getmatch.ru/vacancies",
            )
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch")

    with pytest.raises(BrowserChallengeError):
        _ = [item async for item in GetmatchParser().parse(spec, client)]

    # CareerSiteSource wraps exhausted challenge as challenge_required free text
    # (see site parser terminal path) so public health can map it.
    wrapped = "challenge_required: blocked browser challenge at https://getmatch.ru/vacancies"
    assert extract_public_failure_code(wrapped) == "challenge_required"
    assert public_failure_code_for(GetmatchPageKind.CHALLENGE) == "challenge_required"


@pytest.mark.asyncio
async def test_auth_wall_json_is_explainable() -> None:
    # Force API-style auth wall on listing URL.
    client = _FakeClient(
        {
            "https://getmatch.ru/vacancies": _FakeResponse(
                _read("site_parsers", "getmatch", "auth_wall.json"),
                "https://getmatch.ru/vacancies",
                status_code=401,
                headers={"content-type": "application/json"},
            )
        }
    )
    spec = CareerSiteSpec(url="https://getmatch.ru/vacancies", source_name="getmatch")

    with pytest.raises(GetmatchIngestError) as exc_info:
        _ = [item async for item in GetmatchParser().parse(spec, client)]

    assert exc_info.value.kind == "auth_wall"
    assert public_failure_code_for(exc_info.value) == "auth_wall"
    assert "authentication" in str(exc_info.value).lower() or "auth" in exc_info.value.kind
    assert extract_public_failure_code(str(exc_info.value)) == "auth_wall"


def test_getmatch_terminal_errors_feed_public_failure_reason() -> None:
    """End-to-end of existing health fields → public registry codes.

    Architecture note: SiteParser does not return a diagnostic result object.
    Reasons propagate via GetmatchIngestError.kind / free-text last_error that
    already carries ``"{kind}: …"``. Public sanitizer prefers specific codes
    even when last_error_kind was the generic source_fetch_failed catch-all.
    """
    cases = (
        ("layout_changed", "GetmatchIngestError: layout_changed: sitemap missing locs"),
        ("auth_wall", "GetmatchIngestError: auth_wall: listing requires authentication"),
        (
            "challenge_required",
            "challenge_required: blocked browser challenge at https://getmatch.ru",
        ),
        ("parser_error", "GetmatchIngestError: parser_error: failed to fetch sitemap"),
        ("empty_result", "empty_result"),
    )
    for code, last_error in cases:
        entry = sanitize_source_listing(
            {
                "source_id": "career_site:getmatch",
                "kind": "career_site",
                "enabled": True,
                "status": "failing",
                "degraded": True,
                "public_name": "Getmatch",
                "locator": "https://getmatch.ru/vacancies",
                "last_error_kind": "source_fetch_failed",
                "last_error": last_error,
                "last_run_at": "2026-08-11T12:00:00+00:00",
                "last_success_at": None,
            }
        )
        assert entry is not None, code
        assert entry.public_failure_reason == code, code
        assert entry.status == "degraded"


@pytest.mark.asyncio
async def test_source_health_writer_preserves_getmatch_error_kind(
    tmp_path: Path,
) -> None:
    """TenantRunner last_error_kind extracts allowlisted code from free text."""
    from datetime import UTC, datetime

    from job_ftch.application.pipeline import RunSummary, SourceRunStats
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.config import Settings
    from job_ftch.domain import TenantConfig

    fixture = tmp_path / "fixture.json"
    fixture.write_text("[]", encoding="utf-8")
    tenant_id = "test_getmatch_health_kind"
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": tenant_id,
            "display_name": "Getmatch Health Kind",
            "sources": [{"type": "local_fixture", "path": fixture.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_backend": "sqlite",
            "search_backend": "sqlite",
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=Settings())
    runtime = runner.get_runtime(tenant_id)
    source_id = "career_site:getmatch"
    summary = RunSummary(started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
    summary.by_source_id[source_id] = SourceRunStats(fetched=0, emitted=0, failed=1)
    summary.source_failures.append(
        {
            "source_id": source_id,
            "source_kind": "career_site",
            "source_name": "getmatch",
            "error": "GetmatchIngestError: layout_changed: sitemap missing vacancy loc entries",
        }
    )
    await runner._update_source_health(runtime, summary)
    health = await runtime.store.get_source_health(source_id)
    assert health is not None
    assert health.last_error_kind == "layout_changed"
    assert health.last_error is not None
    assert "layout_changed" in health.last_error
    await runner.close()


def test_build_search_urls_are_runtime_configurable() -> None:
    urls = GetmatchParser().build_search_urls(
        "https://getmatch.ru/",
        ["AI engineer", "ML engineer"],
    )
    assert len(urls) == 1
    assert urls[0].startswith("https://getmatch.ru/vacancies")
    assert "query=" in urls[0]
    assert "AI" in urls[0] or "engineer" in urls[0]


def test_runtime_defaults_do_not_hardcode_core_host_switch() -> None:
    defaults = GetmatchParser().runtime_defaults("https://getmatch.ru/vacancies")
    assert defaults.url_filter is not None
    assert "getmatch" in str(defaults.url_filter)
    assert defaults.include_if_detail_page is True
