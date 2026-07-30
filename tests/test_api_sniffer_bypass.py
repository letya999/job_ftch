"""Tests that api_sniffer.discover() forwards _bypass_strategy into open_page.

The sniffer previously launched a naked, detectable browser (no bypass_strategy
argument passed to open_page at all), unlike the dom monitor which already
forwarded it. This left SPA career sites protected by stealth-tier bypass
strategies fully exposed when routed through api_sniffer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from job_ftch.infrastructure.sources.monitors import api_sniffer
from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope


class _FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.goto_url: str | None = None
        self.route_handler: Any = None

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def route(self, pattern: str, handler: Any) -> None:
        del pattern
        self.route_handler = handler

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        del wait_until
        self.goto_url = url

    async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        del state, timeout


class _RecordingOpenPage:
    """Records the bypass_strategy kwarg passed by discover()."""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._page = _FakePage()

    def __call__(
        self, config: dict[str, Any], *, bypass_strategy: Any = None
    ) -> _RecordingOpenPage:
        self._calls.append({"config": config, "bypass_strategy": bypass_strategy})
        return self

    async def __aenter__(self) -> _FakePage:
        return self._page

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.mark.asyncio
async def test_discover_forwards_bypass_strategy_to_open_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    recorder = _RecordingOpenPage(calls)

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.browser_utils.open_page",
        recorder,
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.check_ssrf",
        AsyncMock(),
    )

    sentinel_strategy = object()
    spec = SimpleNamespace(
        url="https://boards.example.com/jobs",
        monitor_config={
            "_bypass_strategy": sentinel_strategy,
            "settle_seconds": 0,
        },
    )

    result = await api_sniffer.discover(spec, client=None)

    assert len(calls) == 1
    assert calls[0]["bypass_strategy"] is sentinel_strategy
    assert result.urls == set()


@pytest.mark.asyncio
async def test_discover_passes_none_bypass_strategy_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    recorder = _RecordingOpenPage(calls)

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.browser_utils.open_page",
        recorder,
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.check_ssrf",
        AsyncMock(),
    )

    spec = SimpleNamespace(
        url="https://boards.example.com/jobs",
        monitor_config={"settle_seconds": 0},
    )

    await api_sniffer.discover(spec, client=None)

    assert len(calls) == 1
    assert calls[0]["bypass_strategy"] is None


@pytest.mark.asyncio
async def test_route_callback_ignores_target_closed_during_page_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    recorder = _RecordingOpenPage(calls)

    monkeypatch.setattr("job_ftch.infrastructure.sources.browser_utils.open_page", recorder)
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.check_ssrf",
        AsyncMock(),
    )

    spec = SimpleNamespace(
        url="https://boards.example.com/jobs",
        monitor_config={"settle_seconds": 0},
    )
    await api_sniffer.discover(spec, client=None)

    class TargetClosedError(Exception):
        pass

    class ClosingRoute:
        request = SimpleNamespace(url="https://boards.example.com/api/jobs")

        async def continue_(self) -> None:
            raise TargetClosedError()

        async def abort(self) -> None:
            raise AssertionError("the regular route should not be aborted")

    assert recorder._page.route_handler is not None
    await recorder._page.route_handler(ClosingRoute())


@pytest.mark.asyncio
async def test_route_callback_ignores_already_handled_route_during_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    recorder = _RecordingOpenPage(calls)

    monkeypatch.setattr("job_ftch.infrastructure.sources.browser_utils.open_page", recorder)
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.check_ssrf",
        AsyncMock(),
    )
    spec = SimpleNamespace(
        url="https://boards.example.com/jobs",
        monitor_config={"settle_seconds": 0},
    )
    await api_sniffer.discover(spec, client=None)

    class RouteAlreadyHandledError(Exception):
        pass

    class ClosingRoute:
        request = SimpleNamespace(url="https://boards.example.com/api/jobs")

        async def continue_(self) -> None:
            raise RouteAlreadyHandledError("Route.continue_: Route is already handled!")

        async def abort(self) -> None:
            raise AssertionError("the regular route should not be aborted")

    assert recorder._page.route_handler is not None
    await recorder._page.route_handler(ClosingRoute())


@pytest.mark.asyncio
async def test_route_callback_aborts_when_source_deadline_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    recorder = _RecordingOpenPage(calls)

    monkeypatch.setattr("job_ftch.infrastructure.sources.browser_utils.open_page", recorder)
    monkeypatch.setattr(
        "job_ftch.infrastructure.network.ssrf_guard.check_ssrf",
        AsyncMock(),
    )
    spec = SimpleNamespace(
        url="https://boards.example.com/jobs",
        monitor_config={"settle_seconds": 0},
    )
    await api_sniffer.discover(spec, client=None)

    class Route:
        request = SimpleNamespace(url="https://boards.example.com/api/jobs")

        def __init__(self) -> None:
            self.aborted = False

        async def abort(self) -> None:
            self.aborted = True

        async def continue_(self) -> None:
            raise AssertionError("expired source must abort its pending route")

    route = Route()
    assert recorder._page.route_handler is not None
    loop = asyncio.get_running_loop()
    async with source_deadline_scope(loop.time() - 1):
        await recorder._page.route_handler(route)
    assert route.aborted is True
