from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.config import Settings
from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.site_models import MonitorResult, ScrapedPostingPayload
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import (
    CareerSiteSource,
    _looks_like_spa_shell,
    _rank_detail_urls,
    _should_enable_render_on_monitor_retry,
    _should_escalate_empty_monitor,
)
from job_ftch.infrastructure.sources.monitors.shared import (
    BrowserChallengeError,
    raise_if_browser_challenge,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.monkeypatch import MonkeyPatch


@dataclass
class _FakeResponse:
    text: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class _FakeHttpClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True) -> _FakeResponse:
        del follow_redirects
        return self._responses[url]


class _FakeScraperEntry:
    can_handle = None

    def __init__(
        self,
        payload_factory: Callable[[dict[str, Any]], ScrapedPostingPayload | None],
    ) -> None:
        self._payload_factory = payload_factory

    async def factory(
        self,
        url: str,
        scraper_config: dict[str, Any],
        scrape_http: Any,
    ) -> ScrapedPostingPayload | None:
        del url, scrape_http
        return self._payload_factory(scraper_config)


@dataclass
class _CollectedItem:
    url: str


class _NoopBypass:
    current_name = "noop"

    async def apply_http(self, client: object) -> object:
        return client


def test_browser_challenge_is_not_a_listing_page() -> None:
    with pytest.raises(BrowserChallengeError):
        raise_if_browser_challenge(
            "<html><title>Just a moment...</title><body>"
            "Checking your browser before accessing Cloudflare.</body></html>",
            url="https://example.com/jobs",
        )


class _DatedSiteParser:
    has_custom_parse = True
    supports_discover = False

    async def parse(self, spec: CareerSiteSpec, client: object):  # type: ignore[no-untyped-def]
        del spec, client
        for external_id, created_at in (
            ("new", datetime(2026, 6, 1, tzinfo=UTC)),
            ("fresh", datetime(2026, 7, 16, tzinfo=UTC)),
        ):
            yield RawItem(
                source_kind=SourceKind.CAREER_SITE,
                source_name="example",
                external_id=external_id,
                text=f"AI Engineer {external_id}",
                created_at=created_at,
            )


class _RetryingSiteParser:
    has_custom_parse = True
    supports_discover = False

    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, spec: CareerSiteSpec, client: object):  # type: ignore[no-untyped-def]
        del spec, client
        self.calls += 1
        yield RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name="example",
            external_id="same-vacancy",
            text="AI Engineer",
        )
        if self.calls == 1:
            raise RuntimeError("retry after partial parser attempt")
        yield RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name="example",
            external_id="same-vacancy",
            text="AI Engineer duplicate",
        )


class _EmptySiteParser:
    has_custom_parse = True
    supports_discover = False
    parser_name = "empty_special"

    async def parse(self, spec: CareerSiteSpec, client: object):  # type: ignore[no-untyped-def]
        del spec, client
        if False:
            yield


def test_should_enable_render_on_monitor_retry_for_browser_tiers() -> None:
    browser = SimpleNamespace(requires_browser=True)
    http_only = SimpleNamespace(requires_browser=False)
    assert _should_enable_render_on_monitor_retry(browser) is True
    assert _should_enable_render_on_monitor_retry(http_only) is False
    assert _should_enable_render_on_monitor_retry(None) is False


@pytest.mark.parametrize(
    ("has_url_filter", "monitor_suggests_spa", "expected"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_empty_monitor_escalation_requires_explicit_render_evidence(
    has_url_filter: bool, monitor_suggests_spa: bool, expected: bool
) -> None:
    assert (
        _should_escalate_empty_monitor(
            has_url_filter=has_url_filter,
            monitor_suggests_spa=monitor_suggests_spa,
        )
        is expected
    )


@pytest.mark.asyncio
async def test_custom_site_parser_respects_shared_freshness_cutoff(
    monkeypatch: MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://example.com/jobs",
            source_name="example",
            freshness_cutoff_utc=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        http_client=object(),
        auth=MagicMock(),
    )
    source.bypass_strategy = _NoopBypass()
    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser",
        lambda _: _DatedSiteParser(),
    )

    items = [item async for item in source._try_site_parser(object())]

    assert [item.external_id for item in items] == ["fresh"]
    assert source.stats.freshness_filtered == 1
    assert source.stats.to_log_dict()["freshness_filtered"] == 1


@pytest.mark.asyncio
async def test_custom_site_parser_does_not_emit_duplicates_across_retry(
    monkeypatch: MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )
    source.bypass_strategy = _NoopBypass()
    parser = _RetryingSiteParser()
    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser",
        lambda _: parser,
    )
    escalation_calls = 0

    async def _escalate(*args: object, **kwargs: object) -> bool:
        nonlocal escalation_calls
        del args, kwargs
        escalation_calls += 1
        return escalation_calls == 1

    monkeypatch.setattr(source, "_try_escalate_bypass", _escalate)

    items = [item async for item in source._try_site_parser(object())]

    assert [item.external_id for item in items] == ["same-vacancy"]
    assert parser.calls == 2
    assert source.stats.parser_duplicates_suppressed == 1


@pytest.mark.asyncio
async def test_pinned_special_parser_empty_is_terminal(monkeypatch: MonkeyPatch) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://example.com/jobs",
            source_name="example",
            site_parser="empty_special",
        ),
        http_client=object(),
        auth=MagicMock(),
    )
    source.bypass_strategy = _NoopBypass()
    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_site_parser",
        lambda _: _EmptySiteParser(),
    )

    assert [item async for item in source._try_site_parser(object())] == []
    assert source._parser_failure_is_terminal is True
    assert source.stats.requested_parser == "empty_special"
    assert source.stats.actual_parser == "empty_special"
    assert source.stats.monitor_attempts == []


def test_resolve_scraper_chain_prefers_json_ld_for_generic_and_dom() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )

    assert source._resolve_scraper_chain("auto", {}) == [
        "json-ld",
        "embedded",
        "nextdata",
        "dom",
        "maintext",
    ]
    assert source._resolve_scraper_chain("dom", {}) == ["json-ld", "dom", "embedded", "maintext"]


def test_monitor_registry_declares_workable_pair_and_xpath_extension() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://apply.workable.com/acme",
            source_name="workable",
            scraper_config={"xpath_rules": {"title": "//h1/text()"}},
        ),
        http_client=object(),
        auth=MagicMock(),
    )
    assert source._resolve_scraper_chain("workable", {}) == [
        "xpath",
        "workable",
        "json-ld",
        "maintext",
    ]


@pytest.mark.asyncio
async def test_scraper_needs_browser_metadata_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs/1", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )
    source._fetch_detail_html_with_browser = AsyncMock(return_value="<h1>Role</h1>")

    async def scrape(url, config, http):
        del url, http
        return ScrapedPostingPayload(
            title="Role",
            description=config["prefetched_html"],
        )

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda name: SimpleNamespace(
            name=name,
            factory=scrape,
            can_handle=None,
            needs_browser=True,
        ),
    )
    result = await source._run_scraper_chain(
        ["browser-only"],
        "https://example.com/jobs/1",
        None,
        object(),
    )
    assert result is not None
    assert result.description == "<h1>Role</h1>"
    source._fetch_detail_html_with_browser.assert_awaited_once()


def test_rank_detail_urls_keeps_source_detail_url_first() -> None:
    detail_url = "https://hirify.me/jobs/711365-software-engineer-genai-silicon-automation"
    urls = {
        detail_url,
        "https://hirify.me/jobs/650642-ai-compiler-engineer-cpp-llvm",
    }

    ranked = _rank_detail_urls(urls, detail_url)

    assert ranked[0] == detail_url


def test_discover_candidates_caps_overproducing_generic_monitor() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://example.com/jobs",
            source_name="example",
            limit=2,
            detail_limit=2,
        ),
        http_client=object(),
        auth=MagicMock(),
    )
    result = MonitorResult(
        urls={f"https://example.com/jobs/{index}" for index in range(500)},
    )

    candidates = source._discover_candidates(result, "dom")

    assert len(candidates) == 100
    assert source.stats.monitored == 500
    assert source.stats.truncated is True
    assert source.stats.monitor_truncated == 1


def test_discover_candidates_ranks_before_applying_generic_cap() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(
            url="https://example.com/jobs",
            source_name="example",
            limit=2,
            detail_limit=2,
        ),
        http_client=object(),
        auth=MagicMock(),
    )
    detail_url = "https://example.com/jobs/platform-engineer-12345"
    result = MonitorResult(
        urls={detail_url, *{f"https://example.com/blog/post-{index}" for index in range(500)}},
    )

    candidates = source._discover_candidates(result, "sitemap")

    assert [candidate.url for candidate in candidates] == [detail_url]


def test_sitemap_candidates_exclude_non_job_research_urls() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/careers", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )
    result = MonitorResult(
        urls={
            "https://example.com/research/rating/culture-not-money",
            "https://example.com/jobs/platform-engineer-12345",
        }
    )

    candidates = source._discover_candidates(result, "sitemap")

    assert [candidate.url for candidate in candidates] == [
        "https://example.com/jobs/platform-engineer-12345"
    ]


def test_detail_protection_circuit_is_source_local(monkeypatch: MonkeyPatch) -> None:
    settings = Settings.model_validate({"career_site_protection_failure_limit": 2})
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )
    monkeypatch.setattr("job_ftch.config.get_settings", lambda: settings)

    source._record_detail_protection_failure()
    assert source.stats.protection_circuit_open is False
    assert source.stats.zero_reason is not None
    source._record_detail_protection_failure()

    assert source.stats.protection_circuit_open is True
    assert source.stats.zero_reason is not None


def test_looks_like_spa_shell_detects_common_shell_markers() -> None:
    assert _looks_like_spa_shell("<html><body><div id='root'></div></body></html>") is True
    assert (
        _looks_like_spa_shell('<script type="application/ld+json">{"@type":"JobPosting"}</script>')
        is False
    )


def test_editorial_article_metadata_is_detected() -> None:
    from job_ftch.infrastructure.sources.career_site_source import _declares_editorial_article

    assert _declares_editorial_article('<meta property="og:type" content="article">') is True
    assert _declares_editorial_article('<script>{"@type":"JobPosting"}</script>') is False


@pytest.mark.asyncio
async def test_scraper_chain_prefers_description_over_early_title_only_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )

    def title_only(_: dict[str, Any]) -> ScrapedPostingPayload:
        return ScrapedPostingPayload(title="AI Engineer")

    def rich(_: dict[str, Any]) -> ScrapedPostingPayload:
        return ScrapedPostingPayload(
            title="AI Engineer", description="Build production RAG services."
        )

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda name: _FakeScraperEntry(title_only if name == "embedded" else rich),
    )

    payload = await source._run_scraper_chain(
        ["embedded", "maintext"],
        "https://example.com/jobs/1",
        "<html></html>",
        object(),
    )

    assert payload is not None
    assert payload.description == "Build production RAG services."
    assert source.stats.scrape_fallback_used == 1


@pytest.mark.asyncio
async def test_scraper_chain_keeps_title_only_payload_when_no_richer_parser_matches(
    monkeypatch: MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
    )

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda _: _FakeScraperEntry(lambda __: ScrapedPostingPayload(title="AI Engineer")),
    )

    payload = await source._run_scraper_chain(
        ["embedded", "maintext"],
        "https://example.com/jobs/1",
        "<html></html>",
        object(),
    )

    assert payload is not None
    assert payload.title == "AI Engineer"


@pytest.mark.asyncio
async def test_iter_scraped_detail_items_skips_failed_url_and_continues(
    monkeypatch: MonkeyPatch,
) -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example", detail_limit=10),
        http_client=object(),
        auth=MagicMock(),
    )

    async def fake_scrape(
        url: str,
        scraper_chain: list[str],
        source_name: str,
    ) -> RawItem | None:
        del scraper_chain, source_name
        if url.endswith("/2"):
            raise RuntimeError("blocked")
        return cast("RawItem", _CollectedItem(url=url))

    monkeypatch.setattr(source, "_scrape_detail_url_to_raw_item", fake_scrape)

    items = [
        item
        async for item in source._iter_scraped_detail_items(
            ["https://example.com/jobs/1", "https://example.com/jobs/2"],
            ["json-ld"],
            "example",
        )
    ]

    assert [cast("_CollectedItem", item).url for item in items] == ["https://example.com/jobs/1"]
    assert source.stats.scraped == 1


@pytest.mark.asyncio
async def test_scrape_with_fallback_retries_in_browser_for_spa_shell(
    monkeypatch: MonkeyPatch,
) -> None:
    url = "https://example.com/jobs/1"
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=_FakeHttpClient(
            {url: _FakeResponse("<html><body><div id='app'></div></body></html>")}
        ),
        auth=MagicMock(),
    )
    source.bypass_strategy = object()

    browser_fetches: list[str] = []

    async def fake_browser_fetch(target_url: str) -> str | None:
        browser_fetches.append(target_url)
        return "<html><body><h1>Rendered role</h1><p>Rendered description</p></body></html>"

    def payload_factory(scraper_config: dict[str, Any]) -> ScrapedPostingPayload | None:
        html = scraper_config.get("prefetched_html", "")
        if "Rendered role" not in html:
            return None
        return ScrapedPostingPayload(
            title="Rendered role",
            description="Rendered description",
        )

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", fake_browser_fetch)
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda name: _FakeScraperEntry(payload_factory),
    )

    payload = await source._scrape_with_fallback(url, ["json-ld"])

    assert payload is not None
    assert payload.title == "Rendered role"
    assert browser_fetches == [url]


@pytest.mark.asyncio
async def test_scrape_with_fallback_classifies_browser_challenge_as_protection(
    monkeypatch: MonkeyPatch,
) -> None:
    url = "https://example.com/jobs/1"
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=_FakeHttpClient(
            {
                url: _FakeResponse(
                    "<html><title>Attention Required! | Cloudflare</title>"
                    "<body>Enable JavaScript and cookies to continue</body></html>",
                    status_code=403,
                )
            }
        ),
        auth=MagicMock(),
    )
    source.bypass_strategy = object()

    async def fake_browser_fetch(_: str) -> str:
        return (
            "<html><title>Just a moment...</title>"
            "<body>Checking your browser before accessing Cloudflare.</body></html>"
        )

    def scraper_must_not_run(_: dict[str, Any]) -> ScrapedPostingPayload | None:
        raise AssertionError("a browser challenge must not be sent to a detail scraper")

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", fake_browser_fetch)
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda _: _FakeScraperEntry(scraper_must_not_run),
    )

    payload = await source._scrape_with_fallback(url, ["json-ld"])

    assert payload is None
    assert source.stats.detail_protection_failures == 1


@pytest.mark.asyncio
async def test_scrape_with_fallback_retries_title_only_result_in_browser(
    monkeypatch: MonkeyPatch,
) -> None:
    url = "https://example.com/jobs/1"
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=_FakeHttpClient({url: _FakeResponse("<html><h1>AI Engineer</h1></html>")}),
        auth=MagicMock(),
    )

    async def fake_browser_fetch(_: str) -> str:
        return "<html><h1>AI Engineer</h1><p>Build RAG systems.</p></html>"

    def payload_factory(config: dict[str, Any]) -> ScrapedPostingPayload:
        if "Build RAG" in config.get("prefetched_html", ""):
            return ScrapedPostingPayload(title="AI Engineer", description="Build RAG systems.")
        return ScrapedPostingPayload(title="AI Engineer")

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", fake_browser_fetch)
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_scraper",
        lambda _: _FakeScraperEntry(payload_factory),
    )

    payload = await source._scrape_with_fallback(url, ["embedded"])

    assert payload is not None
    assert payload.description == "Build RAG systems."


def _source_with_store(store: Any) -> CareerSiteSource:
    return CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        http_client=object(),
        auth=MagicMock(),
        store=store,
    )


@pytest.mark.asyncio
async def test_filter_unprocessed_urls_keeps_locator_with_processed_history() -> None:
    from job_ftch.domain import SourceKind, processed_key_for_url

    seen_url = "https://example.com/jobs/seen-1"
    fresh_url = "https://example.com/jobs/fresh-2"
    seen_key = processed_key_for_url(SourceKind.CAREER_SITE, "example", seen_url)

    class _Store:
        async def has_processed(self, key: str) -> bool:
            return key == seen_key

    source = _source_with_store(_Store())
    result = await source._filter_unprocessed_urls({seen_url, fresh_url}, source_name="example")

    assert result == {seen_url, fresh_url}


@pytest.mark.asyncio
async def test_filter_unprocessed_urls_fails_open_on_store_error() -> None:
    urls = {"https://example.com/jobs/a", "https://example.com/jobs/b"}

    class _BrokenStore:
        async def has_processed(self, key: str) -> bool:
            raise RuntimeError("store down")

    source = _source_with_store(_BrokenStore())
    result = await source._filter_unprocessed_urls(urls, source_name="example")

    assert result == urls


@pytest.mark.asyncio
async def test_filter_unprocessed_urls_bounds_store_concurrency() -> None:
    from job_ftch.infrastructure.sources import career_site_source as mod

    urls = {f"https://example.com/jobs/{i}" for i in range(200)}

    class _TrackingStore:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def has_processed(self, key: str) -> bool:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)  # yield so overlap can build up
            self.active -= 1
            return False

    store = _TrackingStore()
    source = _source_with_store(store)
    result = await source._filter_unprocessed_urls(urls, source_name="example")

    assert result == urls
    assert store.peak <= mod._PRE_DEDUP_MAX_CONCURRENCY
