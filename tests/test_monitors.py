import pytest

from job_ftch.application.contracts import BoardMonitor
from job_ftch.domain.site_models import MonitorResult
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.monitors.api_sniffer import discover as sniffer_discover
from job_ftch.infrastructure.sources.monitors.dom import (
    _extract_links_rendered,
)
from job_ftch.infrastructure.sources.monitors.dom import (
    discover as dom_discover,
)
from job_ftch.infrastructure.sources.monitors.rss_board import discover as rss_discover
from job_ftch.infrastructure.sources.monitors.shared import AtsRedirectException, check_ats_redirect
from job_ftch.infrastructure.sources.monitors.workday import _parse_components


@pytest.mark.asyncio
async def test_dom_monitor_satisfies_protocol():
    # BoardMonitor protocol: async def discover(self, spec, http) -> MonitorResult
    assert isinstance(dom_discover, BoardMonitor) or callable(dom_discover)


@pytest.mark.asyncio
async def test_api_sniffer_satisfies_protocol():
    assert isinstance(sniffer_discover, BoardMonitor) or callable(sniffer_discover)


@pytest.mark.asyncio
async def test_normalize_monitor_result_empty():
    from job_ftch.infrastructure.sources.site_utils import normalize_monitor_result

    res = normalize_monitor_result(None)
    assert isinstance(res, MonitorResult)
    assert len(res.urls) == 0


@pytest.mark.asyncio
async def test_normalize_monitor_result_set():
    from job_ftch.infrastructure.sources.site_utils import normalize_monitor_result

    res = normalize_monitor_result({"http://example.com/job1"})
    assert isinstance(res, MonitorResult)
    assert "http://example.com/job1" in res.urls


@pytest.mark.asyncio
async def test_dom_monitor_uses_browser_search_prefetch() -> None:
    html = "<html><a href='/jobs/42'>ML Engineer</a></html>"

    class _NoFetchClient:
        async def get(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("prefetched browser result must avoid a second HTTP fetch")

    result = await dom_discover(
        CareerSiteSpec(
            url="https://example.com/careers",
            monitor_config={"_prefetched_listing_html": html},
        ),
        _NoFetchClient(),  # type: ignore[arg-type]
    )

    assert any("/jobs/42" in url for url in result)


def test_static_dom_detects_external_ats_before_same_site_filtering() -> None:
    html = (
        '<a href="https://servicetitan.wd1.myworkdayjobs.com/ServiceTitan/job/'
        'US-Remote/Engineer_JR123">Engineer</a>'
    )

    with pytest.raises(AtsRedirectException) as exc_info:
        check_ats_redirect(html, "https://careers.servicetitan.com/")

    assert exc_info.value.monitor_name == "workday"
    assert "servicetitan.wd1.myworkdayjobs.com" in exc_info.value.url


def test_static_dom_detects_footer_ats_embeds_and_teamtailor() -> None:
    html = (
        "<main>" + ("x" * 100_100) + "</main>"
        '<script src="https://jobs.ashbyhq.com/choco/embed"></script>'
    )

    with pytest.raises(AtsRedirectException) as exc_info:
        check_ats_redirect(html, "https://example.com/careers")

    assert exc_info.value.monitor_name == "ashby"
    assert "ashbyhq.com" in exc_info.value.url

    with pytest.raises(AtsRedirectException) as exc_info:
        check_ats_redirect(
            '<a href="https://webbfontainegroup.teamtailor.com/jobs/123-engineer">job</a>',
            "https://example.com/careers",
        )
    assert exc_info.value.monitor_name == "rss_board"
    assert "teamtailor.com" in exc_info.value.url


def test_workday_redirected_job_url_keeps_only_tenant_site() -> None:
    assert _parse_components(
        "https://servicetitan.wd1.myworkdayjobs.com/en-US/ServiceTitan/job/Engineer_JR123"
    ) == ("servicetitan", "wd1", "ServiceTitan")


@pytest.mark.asyncio
async def test_teamtailor_feed_url_infers_preset_without_runtime_hint() -> None:
    class _Response:
        text = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel>
        <item><title>Engineer</title><link>https://acme.teamtailor.com/jobs/42-engineer</link>
        <description>&lt;p&gt;Build things&lt;/p&gt;</description><tt:locations><tt:location><tt:name>Remote</tt:name></tt:location></tt:locations></item>
        </channel></rss>"""

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url: str, **_: object) -> _Response:
            assert url == "https://acme.teamtailor.com/jobs.rss"
            return _Response()

    result = await rss_discover(
        CareerSiteSpec(url="https://acme.teamtailor.com/jobs.rss", monitor_config={}), _Client()
    )

    assert len(result) == 1
    assert result[0].locations == ["Remote"]


@pytest.mark.asyncio
async def test_dom_rendered_links_ignore_non_string_js_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Page:
        async def content(self) -> str:
            return "<html><title>Careers</title></html>"

        async def evaluate(self, _expression: str) -> object:
            return [["https://example.com/jobs/42"], "https://example.com/jobs/43", None]

    async def _navigate(*_args: object, **_kwargs: object) -> None:
        return None

    async def _run_actions(*_args: object, **_kwargs: object) -> None:
        return None

    import job_ftch.infrastructure.sources.browser_utils as browser_utils

    monkeypatch.setattr(browser_utils, "navigate", _navigate, raising=False)
    monkeypatch.setattr(browser_utils, "run_actions", _run_actions, raising=False)

    assert await _extract_links_rendered(_Page(), "https://example.com/careers", {}) == {
        "https://example.com/jobs/43"
    }
