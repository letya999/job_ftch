from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.career_site_source import (
    CareerSiteSource,
    FetchStats,
    _is_valid_detail_candidate,
)
from job_ftch.infrastructure.sources.embedded_state_utils import (
    extract_inertia_data,
    extract_nuxt_data,
)
from job_ftch.infrastructure.sources.monitors.api_sniffer import (
    _collect_payloads,
)
from job_ftch.infrastructure.sources.monitors.api_sniffer import (
    can_handle as api_sniffer_can_handle,
)
from job_ftch.infrastructure.sources.monitors.dom import (
    LinkExtractor,
    _extract_regex_urls,
    _is_probable_job_link,
    extract_static_job_links,
)
from job_ftch.infrastructure.sources.monitors.dom import (
    discover as dom_discover,
)
from job_ftch.infrastructure.sources.scrapers.dom import can_handle as dom_scraper_can_handle
from job_ftch.infrastructure.sources.scrapers.dom import scrape as dom_scrape
from job_ftch.infrastructure.sources.scrapers.embedded import parse_html
from job_ftch.infrastructure.sources.scrapers.json_ld import scrape as jsonld_scrape
from job_ftch.infrastructure.sources.site_parsers.yandex import (
    _item_from_api as yandex_item_from_api,
)


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeHttpClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self._responses = responses

    async def get(self, url: str, *, follow_redirects: bool = True):  # type: ignore[no-untyped-def]
        del follow_redirects
        return self._responses[url]


class _FakeManagedClient:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    async def __aenter__(self) -> _FakeManagedClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


class _NoFetchClient:
    async def get(self, url: str, *, follow_redirects: bool = True):  # type: ignore[no-untyped-def]
        del url, follow_redirects
        raise AssertionError("network fetch should not happen when prefetched_html is provided")


def test_link_extractor_repairs_cp1251_href_in_utf8_document() -> None:
    # A few legacy boards claim UTF-8 but leave href values in CP1251.  Decode
    # with surrogateescape so those original bytes can be repaired instead of
    # becoming irreversible U+FFFD replacement characters.
    html = b'<a href="/\xe2\xe0\xea\xe0\xed\xf1\xe8\xe8/\xcc\xe8\xed\xf1\xea">Jobs</a>'.decode(
        "utf-8", errors="surrogateescape"
    )
    extractor = LinkExtractor("https://example.test/")

    extractor.feed(html)

    assert extractor.urls == {"https://example.test/вакансии/Минск"}


@pytest.mark.asyncio
async def test_dom_scraper_prefers_body_h2_to_document_title() -> None:
    """A CMS job page may put its posting title in h2 after global navigation."""
    html = """
    <html><head><title>Senior Engineer | Example</title></head><body>
      <nav><div>Back</div><div>Products</div></nav>
      <main><h2>Senior Engineer</h2><p>Build reliable systems with our platform.</p>
      <h3>Apply</h3></main>
    </body></html>
    """

    config = dom_scraper_can_handle([html])

    assert config is not None
    assert config["steps"][0] == {"tag": "h2", "field": "title"}
    result = await dom_scrape(
        "https://example.com/jobs/senior-engineer",
        {**config, "prefetched_html": html},
        _NoFetchClient(),
    )
    assert result is not None
    assert result.title == "Senior Engineer"
    assert "Build reliable systems" in (result.description or "")


def test_challenge_exhaustion_is_distinct_from_an_empty_generic_board() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/careers"),
        http_client=MagicMock(),
        auth=MagicMock(),
    )
    source.stats.detected_monitor_config = {"challenge": True}
    source.bypass_strategy = SimpleNamespace(exhausted=True, escalations_total=2)

    assert source._challenge_bypass_exhausted() is True

    source.bypass_strategy = SimpleNamespace(exhausted=False, escalations_total=2)
    assert source._challenge_bypass_exhausted() is False


def test_observed_challenge_type_is_captured_in_source_stats() -> None:
    source = object.__new__(CareerSiteSource)
    source.stats = FetchStats()
    source.bypass_strategy = SimpleNamespace(observed_challenge_type="cloudflare_challenge")

    source._capture_observed_challenge_type()

    assert source.stats.detected_captcha_types == ["cloudflare_challenge"]
    assert source.stats.challenge_events == [
        {"surface": "monitor", "type": "cloudflare_challenge", "confidence": 0.92}
    ]


@pytest.mark.asyncio
async def test_observed_preflight_challenge_materializes_browser_monitors() -> None:
    source = CareerSiteSource(
        spec=CareerSiteSpec(url="https://example.com/careers", monitor="sitemap"),
        http_client=MagicMock(),
        auth=MagicMock(),
    )
    request_capability = MagicMock(return_value=True)
    source.bypass_strategy = SimpleNamespace(
        observed_challenge_type="cloudflare_challenge",
        request_capability=request_capability,
    )

    monitors, config = await source._resolve_monitors(
        source.http,
        cached_strategy=None,
        monitor_config={},
    )

    assert monitors == ["sitemap", "dom", "api_sniffer"]
    assert config["render"] is True
    request_capability.assert_called_once_with(
        "cloudflare_challenge",
        reason="pre_monitor_observed_challenge",
    )


def test_extract_nuxt_and_inertia_data():
    nuxt_html = """
    <script>
      window.__NUXT__={"job":{"title":"ML Engineer","description":"Build models"}}
    </script>
    """
    inertia_html = """
    <div id="app" data-page="{&quot;component&quot;:&quot;Vacancy&quot;,&quot;props&quot;:{&quot;vacancyList&quot;:{&quot;data&quot;:[{&quot;title&quot;:&quot;DS&quot;,&quot;viewUrl&quot;:&quot;https://example.com/job/1&quot;}]}}}"></div>
    """

    assert extract_nuxt_data(nuxt_html) == {
        "job": {"title": "ML Engineer", "description": "Build models"}
    }
    assert (
        extract_inertia_data(inertia_html)["props"]["vacancyList"]["data"][0]["viewUrl"]
        == "https://example.com/job/1"
    )


def test_embedded_parse_html_auto_detects_nuxt_job_object():
    html = """
    <script>
      window.__NUXT__={
        "payload":{
          "vacancy":{
            "title":"Senior ML Engineer",
            "description":"Own ranking models",
            "location":"Remote"
          }
        }
      }
    </script>
    """

    payload = parse_html(html, {})

    assert payload is not None
    assert payload.title == "Senior ML Engineer"
    assert payload.description == "Own ranking models"
    assert payload.locations == ["Remote"]


@pytest.mark.asyncio
async def test_dom_discover_expands_listing_pages():
    spec = SimpleNamespace(
        url="https://example.com/jobs/data-scientist",
        monitor_config={
            "expand_links": r"example\.com/jobs/data-scientist/[A-Za-z]+",
            "include_if_detail_page": False,
            "url_filter": r"example\.com/jobdesc\?id=\d+",
        },
    )
    client = FakeHttpClient(
        {
            "https://example.com/jobs/data-scientist": FakeResponse(
                '<a href="/jobs/data-scientist/Almaty">Almaty</a>'
            ),
            "https://example.com/jobs/data-scientist/Almaty": FakeResponse(
                '<a href="/jobdesc?id=42">ML Engineer</a>'
            ),
        }
    )

    urls = await dom_discover(spec, client)

    assert urls == {"https://example.com/jobdesc?id=42"}


@pytest.mark.asyncio
async def test_dom_discover_expands_listing_when_only_weak_links_exist():
    spec = SimpleNamespace(
        url="https://example.com/careers",
        monitor_config={"include_if_detail_page": False},
    )
    client = FakeHttpClient(
        {
            "https://example.com/careers": FakeResponse(
                '<a href="/vacancies/engineering">Engineering openings</a>'
            ),
            "https://example.com/vacancies/engineering": FakeResponse(
                '<a href="/vacancy/123">ML Engineer</a>'
            ),
        }
    )

    urls = await dom_discover(spec, client)

    assert "https://example.com/vacancy/123" in urls


@pytest.mark.parametrize(
    "url",
    [
        "https://rabota.by/applicant/vacancy_response?vacancyId=134292606",
        "https://rabota.by/account/signup?backurl=%2Fapplicant",
        "https://hh.ru/search/vacancy?text=AI",
        "https://example.com/auth/login",
        "https://example.com/applicant/resumes/new",
    ],
)
def test_dom_denylist_rejects_non_job_links(url: str):
    assert _is_probable_job_link(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://hirify.me/jobs/84179-senior-software-engineer",
        "https://hh.ru/vacancy/123456789",
        "https://boards.greenhouse.io/abbyy/jobs/55667",
        "https://example.com/jobdesc?id=42",
        # Title slugs whose words merely contain a deny stem must be kept.
        "https://x.com/jobs/accountant-12345",
        "https://x.com/jobs/search-engineer-99",
        "https://x.com/jobs/incident-response-analyst-7",
    ],
)
def test_dom_keeps_real_job_links(url: str):
    assert _is_probable_job_link(url) is True


def test_extract_regex_urls_preserves_host_like_paths():
    html = 'href="hh.ru/vacancy/123" href="career.wb.ru/vacancies/42"'

    urls = _extract_regex_urls(
        html,
        "https://gderabota.ru/vacancies/data-scientist",
        re.compile(r"(hh\.ru/vacancy/\d+|career\.wb\.ru/vacancies/\d+)", re.IGNORECASE),
    )

    assert urls == {
        "https://hh.ru/vacancy/123",
        "https://career.wb.ru/vacancies/42",
    }


def test_generic_detail_candidate_allows_external_teamtailor_ats_jobs():
    assert _is_valid_detail_candidate(
        "https://webbfontainegroup.teamtailor.com/jobs/6485764-senior-devops-engineer",
        "https://webbfontaine.com/about-us/careers",
    )


@pytest.mark.asyncio
async def test_career_site_source_scrapes_detail_page_with_inferred_dom_steps():
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/vacancies/senior-ml-engineer",
        monitor="dom",
        monitor_config={"include_self_url": True},
        limit=5,
        source_name="example_detail",
    )
    html = """
    <html>
      <body>
        <h1>Senior ML Engineer</h1>
        <div>Lead applied ML systems for search and ranking.</div>
      </body>
    </html>
    """
    client = FakeHttpClient(
        {
            "https://example.com/vacancies/senior-ml-engineer": FakeResponse(html),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert items[0].source_name == "example_detail"
    assert "Senior ML Engineer" in items[0].text


@pytest.mark.asyncio
async def test_career_site_source_uses_spec_identity_not_monitor_name():
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/vacancies/123",
        monitor="dom",
        monitor_config={"include_self_url": True},
        limit=5,
    )
    client = FakeHttpClient(
        {
            "https://example.com/vacancies/123": FakeResponse(
                "<h1>ML Engineer</h1><div>Build ML systems.</div>"
            ),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    items = [item async for item in source.fetch()]

    assert len(items) == 1
    assert items[0].source_name == "example_com_vacancies_123"


def test_dom_scraper_can_handle_title_only_detail_pages():
    html = """
    <html>
      <head><title>Risk Data Scientist | T-Bank</title></head>
      <body>
        <p>Risk Data Scientist</p>
        <p>Build scoring models and analyze large data sets.</p>
      </body>
    </html>
    """

    config = dom_scraper_can_handle([html])

    assert config is not None
    assert config["steps"][0]["tag"] == "title"


@pytest.mark.asyncio
async def test_api_sniffer_can_handle_html_with_api_hints():
    client = FakeHttpClient(
        {
            "https://example.com/careers": FakeResponse(
                '<script>fetch("/api/vacancies/search")</script>'
            )
        }
    )

    result = await api_sniffer_can_handle("https://example.com/careers", client)

    assert result == {"browser": True}


def test_api_sniffer_collects_rich_payloads_without_explicit_urls():
    data = {
        "data": {
            "vacancy_item_list": {
                "List": [
                    {
                        "vacancy_id": "97",
                        "title": "Python Backend Engineer",
                        "short_description": "Build backend services",
                        "employment_type_label": "Full time",
                    }
                ]
            }
        }
    }

    payloads = _collect_payloads(data, "https://people.beeline.kz")

    assert set(payloads) == {"https://people.beeline.kz/#job-97"}
    assert payloads["https://people.beeline.kz/#job-97"].title == "Python Backend Engineer"


def test_api_sniffer_ignores_hh_cluster_facet_payloads():
    data = {
        "clusters": [
            {
                "items": [
                    {
                        "name": "Remote",
                        "url": "https://api.hh.ru/vacancies?clusters=true&per_page=0&work_format=REMOTE",
                    }
                ]
            }
        ]
    }

    assert _collect_payloads(data, "https://career.t1.ru/vacancies") == {}


def test_dom_monitor_extracts_job_links_from_jsonld_itemlist():
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "ItemList",
      "itemListElement": [
        {
          "@type": "ListItem",
          "position": 1,
          "url": "https://hirehi.ru/ml-ai/ml-engineer-57417"
        }
      ]
    }
    </script>
    """

    urls = extract_static_job_links(
        html,
        "https://hirehi.ru/",
        url_filter=r"hirehi\.ru/[a-z0-9-]+/[a-z0-9-]+-\d+$",
        limit=3,
    )

    assert urls == ["https://hirehi.ru/ml-ai/ml-engineer-57417"]


def test_yandex_api_item_uses_configured_source_name():
    item = yandex_item_from_api(
        {
            "id": 42,
            "title": "ML Engineer",
            "publication_slug_url": "ml-engineer",
            "short_summary": "Build ranking models",
            "vacancy": {"skills": [{"name": "Python"}], "cities": [{"name": "Moscow"}]},
        },
        "https://yandex.ru/jobs/",
        "ru_yandex_jobs",
    )

    assert item is not None
    assert item.source_name == "ru_yandex_jobs"
    assert str(item.url) == "https://yandex.ru/jobs/ml-engineer"


@pytest.mark.asyncio
async def test_client_for_config_reuses_outer_client_without_skip_ssl():
    outer = object()

    async with client_for_config(outer, {}) as selected:
        assert selected is outer


@pytest.mark.asyncio
async def test_client_for_config_builds_scoped_insecure_client(monkeypatch: pytest.MonkeyPatch):
    built: list[bool] = []

    def fake_build_default_http_client(*, verify_ssl: bool = True) -> _FakeManagedClient:
        built.append(verify_ssl)
        return _FakeManagedClient("insecure")

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site.build_default_http_client",
        fake_build_default_http_client,
    )

    async with client_for_config(object(), {"skip_ssl": True}) as selected:
        assert isinstance(selected, _FakeManagedClient)
        assert selected.marker == "insecure"


@pytest.mark.parametrize(
    "url",
    [
        "https://hh.ru/search/vacancy",
        "https://geekjob.ru/vacancies",
        "https://djinni.co/jobs/",
        "https://jobs.dou.ua/vacancies/",
        "https://playrix.com/job/openings",
        "https://www.superjob.ru/vakansii/",
        "https://welltech.com/career/",
    ],
)
def test_listing_entry_urls_are_not_self_scraped(url: str):
    # Listing/search pages must NOT be scraped as a single job (that yields page
    # chrome like "954 329 вакансий" instead of a vacancy).
    spec = CareerSiteSpec(url=url, source_name="board")
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())
    assert source._should_scrape_source_url() is False


@pytest.mark.parametrize(
    "url",
    [
        "https://hirify.me/jobs/84179-senior-software-engineer-admin",
        "https://hh.ru/vacancy/123456789",
        "https://example.com/jobs/12345",
    ],
)
def test_specific_posting_urls_are_self_scraped(url: str):
    spec = CareerSiteSpec(url=url, source_name="single")
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())
    assert source._should_scrape_source_url() is True


def test_include_self_url_forces_self_scrape_for_listing():
    # Explicit opt-in still works for genuine single-page sources.
    spec = CareerSiteSpec(
        url="https://acme.com/careers",
        source_name="acme",
        monitor_config={"include_self_url": True},
    )
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())
    assert source._should_scrape_source_url() is True


@pytest.mark.asyncio
async def test_career_site_source_does_not_retry_same_monitor_without_real_escalation():
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="example_jobs")
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())

    class _AdaptiveLikeBypass:
        current_name = "noop"

        def handle_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return "timeout"

    source.bypass_strategy = _AdaptiveLikeBypass()

    should_retry = await source._try_escalate_bypass(RuntimeError("timed out"))

    assert should_retry is False


@pytest.mark.asyncio
async def test_career_site_source_retries_same_monitor_after_real_escalation():
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="example_jobs")
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())

    class _AdaptiveLikeBypass:
        current_name = "noop"

        def handle_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.current_name = "curl_stealth"
            return "blocked"

    source.bypass_strategy = _AdaptiveLikeBypass()

    should_retry = await source._try_escalate_bypass(RuntimeError("blocked"))

    assert should_retry is True


@pytest.mark.asyncio
async def test_dom_scrape_uses_prefetched_html_without_network():
    payload = await dom_scrape(
        "https://example.com/job",
        {
            "steps": [
                {"tag": "title", "field": "title"},
                {
                    "tag": "title",
                    "offset": 1,
                    "field": "description",
                    "html": True,
                    "optional": True,
                    "stop_count": 1,
                },
            ],
            "prefetched_html": "<html><head><title>ML Engineer | Example</title></head><body><p>Build models</p></body></html>",
        },
        _NoFetchClient(),
    )

    assert payload is not None
    assert payload.title == "ML Engineer"


@pytest.mark.asyncio
async def test_jsonld_scrape_uses_prefetched_html_without_network():
    payload = await jsonld_scrape(
        "https://example.com/job",
        {
            "prefetched_html": (
                '<script type="application/ld+json">'
                '{"@type":"JobPosting","title":"DS","description":"Train models"}'
                "</script>"
            )
        },
        _NoFetchClient(),
    )

    assert payload is not None
    assert payload.title == "DS"
    assert payload.description == "Train models"


@pytest.mark.asyncio
async def test_captcha_detection_triggers_bypass_escalation(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site", url="https://example.com/job/123", source_name="example", scraper="dom"
    )
    client = FakeHttpClient(
        {
            "https://example.com/job/123": FakeResponse("<div>ddos-guard</div>", status_code=200),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    class _FakeBypass:
        current_name = "noop"

        def __init__(self):
            self.failures = []

        def handle_failure(self, url, status_code, body, error):
            self.failures.append((url, status_code, body))
            self.current_name = "stealth_browser"
            return "captcha"

    source.bypass_strategy = _FakeBypass()

    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return "<html><head><title>Job</title></head><body><p>Desc</p></body></html>"

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", fake_fetch)

    await source._scrape_with_fallback("https://example.com/job/123", ["dom"])

    assert len(source.bypass_strategy.failures) == 1
    assert source.bypass_strategy.failures[0][0] == "https://example.com/job/123"
    assert len(fetched) == 1
    assert fetched[0] == "https://example.com/job/123"


@pytest.mark.asyncio
async def test_render_true_always_triggers_browser_even_when_http_succeeds(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/job/123",
        source_name="example",
        scraper="dom",
        scraper_config={"render": True},
    )
    # HTTP succeeds and returns non-empty response (e.g. SPA shell)
    client = FakeHttpClient(
        {
            "https://example.com/job/123": FakeResponse(
                "<html><body><div id='app'></div></body></html>", status_code=200
            ),
        }
    )
    source = CareerSiteSource(spec=spec, http_client=client, auth=MagicMock())

    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return "<html><body><div id='app'><h1>Job</h1><p>Desc</p></div></body></html>"

    monkeypatch.setattr(source, "_fetch_detail_html_with_browser", fake_fetch)

    await source._scrape_with_fallback("https://example.com/job/123", ["dom"])

    assert len(fetched) == 1
    assert fetched[0] == "https://example.com/job/123"


@pytest.mark.asyncio
async def test_cached_noop_strategy_keeps_adaptive_bypass_enabled(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor="dom",
    )

    class _FakeStore:
        async def get_source_strategy(self, domain: str):  # type: ignore[no-untyped-def]
            assert domain == "example.com"
            return {"monitor": "dom", "bypass": "noop"}

        async def save_source_strategy(
            self,
            domain: str,
            monitor: str,
            bypass: str,
        ):  # type: ignore[no-untyped-def]
            assert domain == "example.com"
            assert monitor == "dom"
            assert bypass == "noop"

    class _FakeMonitor:
        async def discover(self, spec, monitor_http):  # type: ignore[no-untyped-def]
            del spec, monitor_http
            return {"urls": []}

    class _FakeEntry:
        def factory(self, spec, monitor_http, auth):  # type: ignore[no-untyped-def]
            del spec, monitor_http, auth
            return _FakeMonitor()

    source = CareerSiteSource(
        spec=spec,
        http_client=FakeHttpClient({"https://example.com/jobs": FakeResponse("<html></html>")}),
        auth=MagicMock(),
        store=_FakeStore(),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        lambda name: _FakeEntry(),
    )

    items = [item async for item in source.fetch()]

    assert items == []
    assert isinstance(source.bypass_strategy, AdaptiveBypassManager)


@pytest.mark.asyncio
async def test_monitor_receives_runtime_bypass_strategy(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor="dom",
    )

    captured = {}

    class _FakeMonitor:
        async def discover(self, spec, monitor_http):  # type: ignore[no-untyped-def]
            captured["bypass"] = spec.monitor_config.get("_bypass_strategy")
            captured["stats"] = spec.monitor_config.get("_pipeline_stats")
            del monitor_http
            return {"urls": []}

    class _FakeEntry:
        def factory(self, spec, monitor_http, auth):  # type: ignore[no-untyped-def]
            del spec, monitor_http, auth
            return _FakeMonitor()

    source = CareerSiteSource(
        spec=spec,
        http_client=FakeHttpClient({"https://example.com/jobs": FakeResponse("<html></html>")}),
        auth=MagicMock(),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        lambda name: _FakeEntry(),
    )

    items = [item async for item in source.fetch()]

    assert items == []
    assert captured["bypass"] is source.bypass_strategy
    assert captured["stats"] is source.stats


@pytest.mark.asyncio
async def test_monitor_config_can_select_monitor(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor_config={"monitor": "api_sniffer"},
    )

    captured = {}

    class _FakeMonitor:
        async def discover(self, spec, monitor_http):  # type: ignore[no-untyped-def]
            del spec, monitor_http
            return {"urls": []}

    class _FakeEntry:
        def factory(self, spec, monitor_http, auth):  # type: ignore[no-untyped-def]
            del spec, monitor_http, auth
            return _FakeMonitor()

    def _resolve_monitor(name):  # type: ignore[no-untyped-def]
        captured["name"] = name
        return _FakeEntry()

    source = CareerSiteSource(
        spec=spec,
        http_client=FakeHttpClient({"https://example.com/jobs": FakeResponse("<html></html>")}),
        auth=MagicMock(),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        _resolve_monitor,
    )

    items = [item async for item in source.fetch()]

    assert items == []
    assert captured["name"] == "api_sniffer"


@pytest.mark.asyncio
async def test_force_monitor_overrides_explicit_spec_monitor(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor="dom",
        monitor_config={"force_monitor": "api_sniffer", "force_monitor_fallbacks": ["dom"]},
    )

    captured = {}

    class _FakeMonitor:
        async def discover(self, spec, monitor_http):  # type: ignore[no-untyped-def]
            del spec, monitor_http
            return {"urls": []}

    class _FakeEntry:
        def factory(self, spec, monitor_http, auth):  # type: ignore[no-untyped-def]
            del spec, monitor_http, auth
            return _FakeMonitor()

    def _resolve_monitor(name):  # type: ignore[no-untyped-def]
        captured.setdefault("names", []).append(name)
        return _FakeEntry()

    source = CareerSiteSource(
        spec=spec,
        http_client=FakeHttpClient({"https://example.com/jobs": FakeResponse("<html></html>")}),
        auth=MagicMock(),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        _resolve_monitor,
    )

    items = [item async for item in source.fetch()]

    assert items == []
    assert captured["names"] == ["api_sniffer", "dom"]


@pytest.mark.asyncio
async def test_runtime_defaults_can_request_bypass_capability(monkeypatch):
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor="dom",
        monitor_config={
            "bypass_capability": "cloudflare_challenge",
            "bypass_capability_reason": "protected_browser_defaults",
        },
    )
    requested = []

    def _request_capability(self, action, *, reason):  # type: ignore[no-untyped-def]
        del self
        requested.append((action, reason))
        return False

    class _FakeMonitor:
        async def discover(self, spec, monitor_http):  # type: ignore[no-untyped-def]
            del spec, monitor_http
            return {"urls": []}

    class _FakeEntry:
        def factory(self, spec, monitor_http, auth):  # type: ignore[no-untyped-def]
            del spec, monitor_http, auth
            return _FakeMonitor()

    monkeypatch.setattr(
        AdaptiveBypassManager,
        "request_capability",
        _request_capability,
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        lambda name: _FakeEntry(),
    )

    source = CareerSiteSource(
        spec=spec,
        http_client=FakeHttpClient({"https://example.com/jobs": FakeResponse("<html></html>")}),
        auth=MagicMock(),
    )
    items = [item async for item in source.fetch()]

    assert items == []
    assert requested == [("cloudflare_challenge", "protected_browser_defaults")]
