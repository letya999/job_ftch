from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_ftch.application import registry as registry_module
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site_source import CareerSiteSource


def test_bypass_registration_does_not_crash_on_import() -> None:
    from job_ftch.infrastructure.bypass import (
        camoufox_bypass,
        cloak_bypass,
        curl_bypass,
        nodriver_bypass,
        proxy_bypass,
        stealth_browser,
    )

    assert cloak_bypass is not None
    assert camoufox_bypass is not None
    assert curl_bypass is not None
    assert nodriver_bypass is not None
    assert proxy_bypass is not None
    assert stealth_browser is not None


def test_curl_bypass_not_registered_when_curl_cffi_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "job_ftch.infrastructure.bypass.curl_bypass"
    original_module = sys.modules.get(module_name)
    original_factory = registry_module._bypass_factories.pop("curl_stealth", None)

    sys.modules.pop(module_name, None)
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)

    try:
        reloaded = importlib.import_module(module_name)
        assert reloaded._CURL_AVAILABLE is False
        assert "curl_stealth" not in registry_module._bypass_factories
    finally:
        sys.modules.pop(module_name, None)
        if original_module is not None:
            sys.modules[module_name] = original_module
        if original_factory is not None:
            registry_module._bypass_factories["curl_stealth"] = original_factory


@pytest.mark.asyncio
async def test_escalate_to_dead_tier_does_not_crash_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example",
        monitor="dom",
    )

    class _FakeBypass:
        def __init__(self) -> None:
            self.current_name = "curl_stealth"
            self.apply_calls = 0
            self.failures: list[tuple[str, BaseException]] = []

        async def apply_http(self, client: Any) -> Any:
            self.apply_calls += 1
            if self.apply_calls == 1:
                raise ImportError("curl_cffi is not installed")
            return client

        def handle_failure(
            self,
            source_id: str,
            *,
            status_code: int | None = None,
            body: bytes | None = None,
            error: BaseException | None = None,
        ) -> str:
            del status_code, body
            if error is not None:
                self.failures.append((source_id, error))
            self.current_name = "noop"
            return "blocked"

    class _FakeMonitor:
        async def discover(self, spec: Any, monitor_http: Any) -> dict[str, set[str]]:
            del spec, monitor_http
            return {"urls": set()}

    class _FakeMonitorEntry:
        def factory(self, spec: Any, monitor_http: Any, auth: Any) -> _FakeMonitor:
            del spec, monitor_http, auth
            return _FakeMonitor()

    fake_bypass = _FakeBypass()
    source = CareerSiteSource(spec=spec, http_client=object(), auth=MagicMock())

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_bypass",
        lambda name, bypass_config=None: fake_bypass,
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.career_site_source.resolve_monitor",
        lambda name: _FakeMonitorEntry(),
    )

    items = [item async for item in source.fetch()]

    assert items == []
    assert fake_bypass.apply_calls == 2
    assert len(fake_bypass.failures) == 1
    assert fake_bypass.failures[0][0] == "https://example.com/jobs"
