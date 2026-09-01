from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from job_ftch.application.source_assessment import (
    create_source_assessment_service,
    load_source_assessment,
)
from job_ftch.application.tenant_runner import TenantRunner, _apply_runtime_fetch_window
from job_ftch.config import Settings
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
    SourceIngestState,
)
from job_ftch.domain.source_spec import (
    CareerSiteSpec,
    RestAPISourceSpec,
    RSSFeedSourceSpec,
    TelegramChannelSpec,
)
from job_ftch.domain.tenant import TenantConfig
from job_ftch.infrastructure.source_assessment.career_site_probe import (
    CareerSiteAssessmentEngine,
    CareerSiteProbeResult,
    _probe_generic_search,
    _probe_specific_search,
)


class _StateStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls = 0
        self.assessments: dict[tuple[str, str], SourceAssessmentResult] = {}
        self.ingest_states: dict[tuple[str, str], SourceIngestState] = {}

    async def get_run_state(self, key: str, **_: object) -> str | None:
        return self.values.get(key)

    async def set_run_state(self, key: str, value: str, **_: object) -> None:
        self.set_calls += 1
        self.values[key] = value

    async def get_source_assessment(
        self, tenant_id: str, source_id: str
    ) -> SourceAssessmentResult | None:
        return self.assessments.get((tenant_id, source_id))

    async def save_source_assessment(self, tenant_id: str, result: SourceAssessmentResult) -> None:
        self.set_calls += 1
        self.assessments[(tenant_id, result.source_id)] = result

    async def get_source_ingest_state(
        self, tenant_id: str, source_id: str
    ) -> SourceIngestState | None:
        return self.ingest_states.get((tenant_id, source_id))

    async def save_source_ingest_state(self, tenant_id: str, state: SourceIngestState) -> None:
        self.ingest_states[(tenant_id, state.source_id)] = state


def _runner() -> TenantRunner:
    settings = Settings(
        llm_backend="heuristic",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
    )
    tenant = TenantConfig(
        tenant_id="assessment",
        display_name="Assessment",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        llm_backend="heuristic",
    )
    return TenantRunner.from_tenants([tenant], base_settings=settings)


def _probe_result(
    *,
    has_publication_time: bool = False,
    has_update_time: bool = False,
    supports_ordered_head: bool = False,
    has_change_validators: bool = False,
    has_page_change_signal: bool = False,
    has_rss_or_sitemap_dates: bool = False,
) -> CareerSiteProbeResult:
    capabilities = SourceCapabilities(
        source_family="generic_site",
        has_stable_url=True,
        has_publication_time=has_publication_time,
        has_update_time=has_update_time,
        supports_ordered_head=supports_ordered_head,
        has_change_validators=has_change_validators,
        has_page_change_signal=has_page_change_signal,
        has_rss_or_sitemap_dates=has_rss_or_sitemap_dates,
    )
    if has_publication_time or has_update_time:
        freshness = FreshnessAssessment(
            confidence=AssessmentConfidence.MEDIUM,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            ordered_by_newest=supports_ordered_head,
            requires_full_snapshot=False,
            rationale="test item dates",
        )
    elif supports_ordered_head:
        freshness = FreshnessAssessment(
            confidence=AssessmentConfidence.MEDIUM,
            can_detect_freshness_without_snapshot=True,
            ordered_by_newest=True,
            requires_full_snapshot=False,
            rationale="test ordered head",
        )
    elif has_page_change_signal or has_rss_or_sitemap_dates:
        freshness = FreshnessAssessment(
            confidence=AssessmentConfidence.MEDIUM,
            can_detect_freshness_without_snapshot=True,
            page_level_change_only=True,
            requires_full_snapshot=False,
            rationale="test page change",
        )
    else:
        freshness = FreshnessAssessment(
            confidence=AssessmentConfidence.MEDIUM,
            requires_full_snapshot=True,
            rationale="test no signal",
        )
    return CareerSiteProbeResult(capabilities=capabilities, evidence=(), freshness=freshness)


@pytest.mark.asyncio
async def test_specific_search_assessment_rejects_ignored_query() -> None:
    base_url = "https://example.com/jobs"
    base_html = (
        '<h1>Engineer</h1><a href="/jobs/1">Engineer</a>'
        '<a href="/jobs/2">Manager</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query).get("q", [""])[0]
        if query == "zzqxnonsense7391":
            body = base_html
        elif query:
            body = '<h1>Engineer</h1><a href="/jobs/1">Engineer</a>'
        else:
            body = base_html
        return httpx.Response(200, text=body, request=request)

    class Parser:
        parser_name = "example_parser"

        def build_search_urls(self, base: str, keywords: list[str], *, limit: int | None = None):
            del limit
            return [f"{base}?q={'+'.join(keywords)}"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _probe_specific_search(
            CareerSiteSpec(url=base_url), Parser(), client, base_html, base_url
        )

    assert result is not None
    assert result.executor.value == "specific_url"
    assert result.status.value == "verified"


@pytest.mark.asyncio
async def test_generic_post_search_assessment_is_available_for_specific_surface() -> None:
    base_url = "https://example.com/jobs"
    base_html = (
        '<form method="post" action="/search">'
        '<input type="search" name="q"><input type="hidden" name="csrf" value="safe">'
        '</form><a href="/jobs/1">Engineer</a><a href="/jobs/2">Manager</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            values = parse_qs(request.content.decode())
            query = values.get("q", [""])[0]
            if query == "zzqxnonsense7391":
                body = "<form method='post'><input type='search' name='q'></form>"
            else:
                body = "<form method='post'><input type='search' name='q'></form><a href='/jobs/1'>Engineer</a>"
        else:
            body = base_html
        return httpx.Response(200, text=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _probe_generic_search(client, base_url, base_html, ["Engineer", "Manager"])

    assert result.executor.value == "generic_post"
    assert result.status.value == "verified"
    assert result.query_param == "q"


@pytest.mark.asyncio
async def test_assessment_marks_telegram_as_item_level_freshness() -> None:
    service = create_source_assessment_service()

    result = await service.assess(TelegramChannelSpec(entity="@jobs", source_name="jobs"))

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.can_filter_since_yesterday is True
    assert result.freshness.item_level_dates is True
    assert result.capabilities.has_publication_time is True


@pytest.mark.asyncio
async def test_assessment_marks_rss_as_item_level_freshness() -> None:
    service = create_source_assessment_service()

    result = await service.assess(RSSFeedSourceSpec(feed_url="https://example.com/feed.xml"))

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.can_filter_since_yesterday is True
    assert result.freshness.item_level_dates is True
    assert result.capabilities.has_rss_or_sitemap_dates is True


@pytest.mark.asyncio
async def test_assessment_marks_known_api_as_since_capable() -> None:
    service = create_source_assessment_service()
    spec = RestAPISourceSpec(
        base_url="https://api.hh.ru/",
        jobs_endpoint="vacancies",
        source_name="hh",
    )

    result = await service.assess(spec)

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.can_filter_since_yesterday is True
    assert result.capabilities.has_cursor_or_since_filter is True


@pytest.mark.asyncio
async def test_assessment_known_career_site_uses_registry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result(supports_ordered_head=True)

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://hh.ru/search/vacancy", source_name="hh_search")
    )

    assert result.capabilities.known_integration is True
    assert result.evidence[0].kind == "known_site"
    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.ordered_by_newest is True
    assert result.freshness.requires_full_snapshot is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "evidence_value"),
    [
        ("https://jobs.lever.co/openai", "lever_board"),
        ("https://boards.greenhouse.io/airbnb", "greenhouse"),
        ("https://jobs.ashbyhq.com/Anthropic", "ashby"),
        ("https://apply.workable.com/acme", "workable"),
        ("https://acme.wd1.myworkdayjobs.com/External", "workday"),
        ("https://acme.jobs.personio.de/", "personio"),
        ("https://acme.recruitee.com/", "recruitee"),
        ("https://jobs.deel.com/acme", "deel"),
    ],
)
async def test_assessment_known_url_shapes_use_registry_hints(
    url: str, evidence_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(CareerSiteSpec(url=url, source_name="known_shape"))

    assert result.capabilities.known_integration is True
    assert result.evidence[0].value == evidence_value


@pytest.mark.asyncio
async def test_assessment_generic_site_defaults_to_snapshot_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://example.com/careers", source_name="careers")
    )

    assert result.freshness.can_detect_freshness_without_snapshot is False
    assert result.freshness.can_filter_since_yesterday is False
    assert result.freshness.requires_full_snapshot is True


@pytest.mark.asyncio
async def test_assessment_treats_registry_known_site_hints_as_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.registry import known_board_assessment_hint
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    monkeypatch.setattr(
        builtin,
        "resolve_site_parser_assessment_hint",
        lambda url: known_board_assessment_hint(
            "known_site",
            f"hint:{url}",
            requires_full_snapshot=False,
        ),
    )
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://example.com/jobs", source_name="example_jobs")
    )

    assert result.capabilities.known_integration is True
    assert result.capabilities.source_family == "known_board"


@pytest.mark.asyncio
async def test_assessment_upgrades_generic_site_when_probe_finds_item_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result(
            has_publication_time=True,
            has_update_time=True,
        )

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://example.com/careers", source_name="careers")
    )

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.can_filter_since_yesterday is True
    assert result.freshness.item_level_dates is True
    assert result.freshness.requires_full_snapshot is False


@pytest.mark.asyncio
async def test_assessment_upgrades_generic_site_when_probe_finds_ordered_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result(supports_ordered_head=True)

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://example.com/careers", source_name="careers")
    )

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.can_filter_since_yesterday is False
    assert result.freshness.ordered_by_newest is True
    assert result.freshness.requires_full_snapshot is False


@pytest.mark.asyncio
async def test_assessment_upgrades_generic_site_when_probe_finds_page_change_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result(
            has_change_validators=True,
            has_page_change_signal=True,
        )

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()

    result = await service.assess(
        CareerSiteSpec(url="https://example.com/careers", source_name="careers")
    )

    assert result.freshness.can_detect_freshness_without_snapshot is True
    assert result.freshness.page_level_change_only is True
    assert result.freshness.can_filter_since_yesterday is False
    assert result.freshness.requires_full_snapshot is False


@pytest.mark.asyncio
async def test_career_site_engine_uses_bounded_detail_sample_scraper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.registry import ScraperEntry
    from job_ftch.infrastructure.source_assessment import career_site_probe
    from job_ftch.infrastructure.sources.site_fingerprinter import SiteClass, SiteProfile

    class _Response:
        def __init__(self, url: str, text: str) -> None:
            self.url = url
            self.text = text
            self.status_code = 200
            self.headers: dict[str, str] = {}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> _Response:
            if url.endswith("/jobs/1"):
                return _Response(url, "<html><script type='application/ld+json'>{}</script></html>")
            return _Response(
                url,
                "<html><a href='/jobs/1'>Backend Engineer</a></html>",
            )

    class _AsyncClientFactory:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return _Client()

        async def __aexit__(self, *_: object) -> None:
            return None

    async def _fake_fingerprint(url: str, client: object) -> SiteProfile:
        del url, client
        return SiteProfile(SiteClass.UNKNOWN, ["dom"], {})

    async def _scrape(url: str, config: dict[str, object], client: object) -> ScrapedPostingPayload:
        del url, config, client
        return ScrapedPostingPayload(title="Backend Engineer", date_posted="2026-06-27")

    monkeypatch.setattr(career_site_probe.httpx, "AsyncClient", _AsyncClientFactory)
    monkeypatch.setattr(career_site_probe, "fingerprint", _fake_fingerprint)
    monkeypatch.setattr(career_site_probe, "get_all_monitor_entries", lambda: [])
    monkeypatch.setattr(
        career_site_probe,
        "get_all_scraper_entries",
        lambda: [
            ScraperEntry(
                name="json-ld",
                factory=_scrape,
                can_handle=lambda _htmls: {},
            )
        ],
    )

    result = await CareerSiteAssessmentEngine().assess(
        CareerSiteSpec(url="https://example.com/careers", source_name="careers")
    )

    assert result.freshness.can_filter_since_yesterday is True
    assert result.freshness.item_level_dates is True
    assert result.freshness.requires_full_snapshot is False


@pytest.mark.asyncio
async def test_assessment_persists_and_reuses_cached_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()
    store = _StateStore()
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")

    first = await service.assess_and_store(spec, store)  # type: ignore[arg-type]
    set_calls_after_first = store.set_calls
    second = await service.assess_and_store(spec, store)  # type: ignore[arg-type]

    assert second == first
    assert store.set_calls == set_calls_after_first
    assert await load_source_assessment(store, first.source_id) == first  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_assessment_cache_is_invalidated_when_spec_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    calls = 0

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        nonlocal calls
        del self, spec
        calls += 1
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    service = create_source_assessment_service()
    store = _StateStore()
    first_spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")
    second_spec = first_spec.model_copy(update={"url": "https://example.com/careers"})

    await service.assess_and_store(first_spec, store)  # type: ignore[arg-type]
    await service.assess_and_store(second_spec, store)  # type: ignore[arg-type]

    assert calls == 2
    assert store.set_calls == 2


@pytest.mark.asyncio
async def test_tenant_runner_returns_freshness_assessment_for_added_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.source_assessment import builtin

    async def _fake_assess(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        return _probe_result()

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _fake_assess)
    runner = _runner()
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")

    try:
        added = await runner.add_source_spec("assessment", spec, added_via="test")
        listed = await runner.list_sources("assessment")
    finally:
        await runner.close()

    assert added["assessment"]["status"] == "assessed"
    assert added["assessment"]["can_detect_freshness_without_snapshot"] is False
    assert added["assessment"]["requires_full_snapshot"] is True
    assert any(item["assessment"]["status"] == "assessed" for item in listed)


@pytest.mark.asyncio
async def test_listing_sources_does_not_probe_unassessed_career_sites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status command must remain responsive while probes run only before ingest."""
    from job_ftch.infrastructure.source_assessment import builtin

    async def _unexpected_probe(self: object, spec: object) -> CareerSiteProbeResult:
        del self, spec
        raise AssertionError("listing sources must not run a network assessment")

    monkeypatch.setattr(builtin.CareerSiteAssessmentEngine, "assess", _unexpected_probe)
    settings = Settings(
        llm_backend="heuristic",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
    )
    tenant = TenantConfig(
        tenant_id="listing",
        display_name="Listing",
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        llm_backend="heuristic",
        sources=[CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")],
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    try:
        listed = await runner.list_sources("listing")
    finally:
        await runner.close()

    assert listed[0]["assessment"] == {"status": "missing"}


def test_runtime_fetch_window_bootstrap_max_items_caps_generic_site() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        initial_ingest_mode="max_items",
        initial_ingest_max_items=50,
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=None,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )

    assert effective.limit == 50
    assert effective.detail_limit == 50
    assert effective.freshness_cutoff_utc is None


def test_runtime_fetch_window_bootstrap_uses_initial_limit_before_regular_limit() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        limit=10,
        detail_limit=10,
        initial_ingest_mode="max_items",
        initial_ingest_max_items=50,
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=None,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )

    assert effective.limit == 50
    assert effective.detail_limit == 50
    assert effective.freshness_cutoff_utc is None


def test_runtime_fetch_window_after_bootstrap_uses_regular_limit_for_snapshot_site() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        limit=10,
        detail_limit=10,
        initial_ingest_mode="max_items",
        initial_ingest_max_items=50,
    )
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=None,
        bootstrap_completed_at=now - timedelta(days=1),
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit == 10
    assert effective.detail_limit == 10
    assert effective.freshness_cutoff_utc is None


def test_runtime_fetch_window_bootstrap_week_uses_cutoff_when_freshness_proven() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        initial_ingest_mode="lookback_window",
        initial_ingest_lookback_seconds=7 * 24 * 60 * 60,
    )
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit is None
    assert effective.freshness_cutoff_utc == now - timedelta(days=7)


def test_runtime_fetch_window_bootstrap_auto_prefers_week_when_freshness_proven() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        initial_ingest_lookback_seconds=7 * 24 * 60 * 60,
    )
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit is None
    assert effective.detail_limit is None
    assert effective.freshness_cutoff_utc == now - timedelta(days=7)


def test_runtime_fetch_window_bootstrap_time_window_clears_existing_limits() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        limit=10,
        detail_limit=10,
        initial_ingest_lookback_seconds=7 * 24 * 60 * 60,
    )
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit is None
    assert effective.detail_limit is None
    assert effective.freshness_cutoff_utc == now - timedelta(days=7)


def test_runtime_fetch_window_incremental_uses_schedule_interval_cutoff() -> None:
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=now - timedelta(days=1),
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.freshness_cutoff_utc == now - timedelta(hours=4)
    assert effective.limit is None


def test_runtime_fetch_window_incremental_prefers_last_started_cutoff() -> None:
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    last_started = now - timedelta(days=2)
    last_success = now - timedelta(days=1, hours=23)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=now - timedelta(days=7),
        last_started_at=last_started,
        last_successful_run_at=last_success,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.freshness_cutoff_utc == last_started
    assert effective.limit is None


def test_runtime_fetch_window_incremental_clears_existing_limits_and_clamps_future_cutoff() -> None:
    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        limit=5,
        detail_limit=5,
    )
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    future_success = now + timedelta(hours=2)
    assessment = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=now - timedelta(days=1),
        last_successful_run_at=future_success,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit is None
    assert effective.detail_limit is None
    assert effective.freshness_cutoff_utc == now


def test_runtime_fetch_window_bootstrap_week_applies_to_telegram_sources() -> None:
    spec = TelegramChannelSpec(entity="@example", source_name="jobs", limit=50)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="telegram_channel:jobs",
        source_type="telegram_channel",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=False,
            item_level_dates=True,
            ordered_by_newest=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=None,
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.limit is None
    assert effective.freshness_cutoff_utc == now - timedelta(days=7)


def test_runtime_fetch_window_incremental_applies_to_rss_sources() -> None:
    spec = RSSFeedSourceSpec(feed_url="https://example.com/feed.xml", source_name="feed")
    now = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    assessment = SourceAssessmentResult(
        source_id="rss_feed:feed",
        source_type="rss_feed",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=False,
            item_level_dates=True,
            ordered_by_newest=True,
            requires_full_snapshot=False,
            rationale="test",
        ),
    )

    effective = _apply_runtime_fetch_window(
        spec,
        assessment=assessment,
        bootstrap_completed_at=now - timedelta(days=1),
        interval_seconds=4 * 60 * 60,
        now=now,
    )

    assert effective.freshness_cutoff_utc == now - timedelta(hours=4)


def test_career_site_source_filters_new_payloads_by_cutoff() -> None:
    from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource

    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        freshness_cutoff_utc=datetime(2026, 6, 27, 0, 0, tzinfo=UTC),
    )
    source = CareerSiteSource(spec, http_client=object(), auth=object())

    assert source._passes_freshness_cutoff("2026-06-27T10:00:00Z") is True
    assert source._passes_freshness_cutoff("2026-06-20T10:00:00Z") is False
    assert source._passes_freshness_cutoff(None) is True
    assert source.stats.freshness_filtered == 1
    assert source.stats.freshness_undated_passed == 1


def test_career_site_source_rejects_undated_items_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource

    spec = CareerSiteSpec(
        url="https://example.com/jobs",
        source_name="jobs",
        freshness_cutoff_utc=datetime(2026, 6, 27, 0, 0, tzinfo=UTC),
    )
    source = CareerSiteSource(spec, http_client=object(), auth=object())
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: Settings(freshness_require_date=True),
    )

    assert source._passes_freshness_cutoff(None) is False
    assert source.stats.freshness_filtered == 1
    assert source.stats.freshness_undated_passed == 0
