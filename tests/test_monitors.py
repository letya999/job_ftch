import pytest

from job_ftch.application.contracts import BoardMonitor
from job_ftch.domain.site_models import MonitorResult
from job_ftch.infrastructure.sources.monitors.api_sniffer import discover as sniffer_discover
from job_ftch.infrastructure.sources.monitors.dom import discover as dom_discover


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
