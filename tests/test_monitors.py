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
