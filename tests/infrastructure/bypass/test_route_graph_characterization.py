"""Characterization tests for ADR-074 route-graph integration gaps."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from job_ftch.infrastructure.bypass import adaptive
from job_ftch.infrastructure.bypass.adaptive import AdaptiveBypassManager
from job_ftch.infrastructure.bypass.failure_signal import FailureKind, HeuristicFailureSignal
from job_ftch.infrastructure.sources.career_site_source import (
    CareerSiteSource,
    _should_enable_render_on_monitor_retry,
)


class _MutationStrategy:
    async def apply_http(self, client: Any) -> Any:
        return ("strategy", client)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        del page


class _SessionStrategy(_MutationStrategy):
    def __init__(self) -> None:
        self.opened = False

    @asynccontextmanager
    async def open_page(self, config: dict[str, Any], *, use_proxy: bool = False):
        self.opened = True
        yield {"config": config, "use_proxy": use_proxy}


@pytest.mark.asyncio
async def test_adaptive_manager_delegates_session_owned_open_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_strategy = _SessionStrategy()

    def resolve(name: str, bypass_config: Any = None) -> Any:
        del bypass_config
        if name == "nodriver":
            return session_strategy
        return _MutationStrategy()

    monkeypatch.setattr(adaptive, "resolve_bypass", resolve)
    manager = AdaptiveBypassManager()
    assert manager.escalate_to("nodriver")

    async with manager.open_page({"timeout": 1000}, use_proxy=True) as page:
        assert page["use_proxy"] is True

    assert session_strategy.opened is True


@pytest.mark.asyncio
async def test_career_site_http_path_applies_context_and_strategy() -> None:
    with patch(
        "job_ftch.infrastructure.bypass.adaptive.resolve_bypass",
        return_value=_MutationStrategy(),
    ):
        controller = AdaptiveBypassManager()
    source = object.__new__(CareerSiteSource)
    source.bypass_strategy = controller
    source._bypass_ctx = MagicMock()
    source._bypass_ctx.apply_http = AsyncMock(return_value="context-client")
    source._temporary_http_clients = []
    controller.bind_context(source._bypass_ctx)

    result = await source._apply_bypass_http("base-client")

    source._bypass_ctx.apply_http.assert_awaited_once_with("base-client", use_proxy=False)
    assert result == ("strategy", "context-client")


@pytest.mark.asyncio
async def test_career_site_http_path_supplies_domain_to_pinned_proxy() -> None:
    class Client:
        pass

    class Strategy:
        async def apply_http(self, client: Any) -> Any:
            assert client._domain_hint == "career.example.com"
            return client

    source = object.__new__(CareerSiteSource)
    source.spec = SimpleNamespace(url="https://career.example.com/jobs")
    source.bypass_strategy = Strategy()
    source._temporary_http_clients = []
    client = Client()

    assert await source._apply_bypass_http(client) is client


@pytest.mark.parametrize("owns_session", [False, True])
def test_all_browser_capable_retry_states_enable_render(owns_session: bool) -> None:
    strategy = SimpleNamespace(requires_browser=True, owns_session=owns_session)
    assert _should_enable_render_on_monitor_retry(strategy) is True


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (403, b"<div class='cf-turnstile'>Checking your browser</div>", FailureKind.CHALLENGE),
        (503, b"<script src='/cdn-cgi/challenge-platform/x'></script>", FailureKind.CHALLENGE),
        (503, b"ordinary upstream failure", FailureKind.SERVER_ERROR),
    ],
)
def test_status_body_classification_precedes_status_fallback(
    status: int,
    body: bytes,
    expected: FailureKind,
) -> None:
    assert HeuristicFailureSignal().classify(status_code=status, body=body, error=None) is expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectTimeout("slow"), FailureKind.TIMEOUT),
        (httpx.ConnectError("certificate verify failed"), FailureKind.TLS_ERROR),
        (httpx.ConnectError("name or service not known"), FailureKind.DNS_ERROR),
        (httpx.ConnectError("connection refused"), FailureKind.CONNECT_ERROR),
        (ValueError("invalid selector"), FailureKind.PARSER_ERROR),
    ],
)
def test_transport_and_parser_errors_remain_distinct(
    error: BaseException,
    expected: FailureKind,
) -> None:
    assert HeuristicFailureSignal().classify(status_code=None, body=None, error=error) is expected


@pytest.mark.asyncio
async def test_prepared_fingerprinter_client_is_not_rewrapped() -> None:
    from job_ftch.infrastructure.sources import site_fingerprinter

    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.com/jobs"),
        text="<html><body>" + ("jobs " * 100) + "</body></html>",
    )
    client = AsyncMock()
    client.get.return_value = response

    with patch(
        "job_ftch.application.registry.resolve_bypass",
        side_effect=AssertionError("prepared client must retain its selected route"),
    ):
        await site_fingerprinter.fingerprint("https://example.com/jobs", client)

    client.get.assert_awaited_once()
