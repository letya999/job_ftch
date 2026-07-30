"""HTTP hygiene tests: UA/Accept defaults, TLS context, and retry policy.

Covers the career-site default HTTP client (realistic Chrome UA/Accept,
hardened SSL context, extended retry-on-status set, bounded soft-403 retry)
and the browser-utils Chrome UA bump / in-page fetch helper.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from job_ftch.infrastructure.sources import browser_utils
from job_ftch.infrastructure.sources.career_site import (
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    _is_empty_200_response,
    _is_retryable_http_error,
    _is_soft_403_error,
    _make_ssl_context,
    _RetryingHttpClient,
    build_default_http_client,
)
from job_ftch.infrastructure.sources.ssrf_guard import SSRFGuardedTransport, _host_is_private


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/jobs")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429, 408, 425])
def test_is_retryable_http_error_covers_extended_status_set(status_code: int) -> None:
    assert _is_retryable_http_error(_status_error(status_code)) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 410])
def test_is_retryable_http_error_excludes_non_retryable_statuses(status_code: int) -> None:
    assert _is_retryable_http_error(_status_error(status_code)) is False


def test_is_retryable_http_error_covers_network_and_timeout_errors() -> None:
    request = httpx.Request("GET", "https://example.com/jobs")
    assert _is_retryable_http_error(httpx.TimeoutException("timed out", request=request)) is True
    assert _is_retryable_http_error(httpx.ConnectError("refused", request=request)) is True


@pytest.mark.parametrize("status_code", [401, 403])
def test_is_soft_403_error_matches_401_and_403(status_code: int) -> None:
    assert _is_soft_403_error(_status_error(status_code)) is True


@pytest.mark.parametrize("status_code", [400, 404, 429, 500])
def test_is_soft_403_error_rejects_other_statuses(status_code: int) -> None:
    assert _is_soft_403_error(_status_error(status_code)) is False


def test_make_ssl_context_uses_certifi_and_hardening_options() -> None:
    ctx = _make_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.options & ssl.OP_NO_TICKET
    op_legacy_server_connect = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    assert ctx.options & op_legacy_server_connect


def test_build_default_http_client_sets_chrome_headers() -> None:
    client = build_default_http_client()

    headers = client._client.headers
    assert headers["User-Agent"] == DEFAULT_USER_AGENT
    assert headers["Accept"] == DEFAULT_ACCEPT
    assert "Chrome/13" in DEFAULT_USER_AGENT


def test_build_default_http_client_uses_hardened_ssl_context_by_default() -> None:
    client = build_default_http_client()

    # SSRFGuardedTransport wraps the real transport; SSL context is one level deeper.
    verify = client._client._transport._wrapped._pool._ssl_context
    assert isinstance(verify, ssl.SSLContext)


def test_build_default_http_client_skip_ssl_disables_verification() -> None:
    client = build_default_http_client(verify_ssl=False)

    verify = client._client._transport._wrapped._pool._ssl_context
    # httpx still builds an SSLContext internally even with verify=False,
    # but certificate verification is disabled on it.
    assert verify.verify_mode == ssl.CERT_NONE


def test_build_default_http_client_wraps_ssrf_guarded_transport() -> None:
    from job_ftch.infrastructure.sources.ssrf_guard import SSRFGuardedTransport

    client = build_default_http_client()
    assert isinstance(client._client._transport, SSRFGuardedTransport)


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient driving canned responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def get(self, url: str, *, follow_redirects: bool = False, **kwargs: Any) -> Any:
        del url, follow_redirects, kwargs
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def post(self, url: str, *, follow_redirects: bool = False, **kwargs: Any) -> Any:
        return await self.get(url, follow_redirects=follow_redirects, **kwargs)


@dataclass
class _FakeResponse:
    status_code: int
    _raise: Exception | None = field(default=None)
    headers: dict[str, str] = field(default_factory=dict)
    request: httpx.Request | None = field(default=None)

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise


def _fake_response(status_code: int) -> _FakeResponse:
    request = httpx.Request("GET", "https://example.com/jobs")
    if status_code < 400:
        return _FakeResponse(status_code, request=request)
    return _FakeResponse(status_code, _raise=_status_error(status_code), request=request)


@pytest.mark.asyncio
async def test_retrying_client_retries_extended_statuses_then_succeeds() -> None:
    fake = _FakeAsyncClient([_fake_response(503), _fake_response(200)])
    client = _RetryingHttpClient(fake, max_retries=2, retry_delay_seconds=0.0)

    response = await client.get("https://example.com/jobs")

    assert response.status_code == 200
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_retrying_client_soft_403_retries_once_on_same_client() -> None:
    fake = _FakeAsyncClient([_fake_response(403), _fake_response(200)])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    response = await client.get("https://example.com/jobs")

    assert response.status_code == 200
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_retrying_client_soft_403_retry_is_bounded_to_one_attempt() -> None:
    fake = _FakeAsyncClient([_fake_response(403), _fake_response(403), _fake_response(200)])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("https://example.com/jobs")

    # First call + one bounded soft-403 retry, then the persistent 403 raises
    # without masking the hard block as transient.
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_retrying_client_raises_on_non_retryable_status() -> None:
    fake = _FakeAsyncClient([_fake_response(400)])
    client = _RetryingHttpClient(fake, max_retries=2, retry_delay_seconds=0.0)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("https://example.com/jobs")

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retrying_client_passes_through_extra_get_kwargs() -> None:
    fake = _FakeAsyncClient([_fake_response(200)])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    response = await client.get(
        "https://example.com/jobs",
        params={"page": "1"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retrying_client_supports_post_requests() -> None:
    fake = _FakeAsyncClient([_fake_response(200)])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    response = await client.post(
        "https://example.com/jobs/search",
        json={"page": 1},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retrying_client_delegates_retry_budget_to_fetch_with_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeAsyncClient([])
    client = _RetryingHttpClient(fake, max_retries=2, retry_delay_seconds=0.0)
    captured: dict[str, Any] = {}

    async def _fake_fetch_with_retry(
        http: object,
        url: str,
        *,
        follow_redirects: bool = True,
        max_attempts: int = 3,
        **kwargs: Any,
    ) -> _FakeResponse:
        captured.update(
            {
                "http": http,
                "url": url,
                "follow_redirects": follow_redirects,
                "max_attempts": max_attempts,
                "kwargs": kwargs,
            }
        )
        return _fake_response(200)

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.http_retry.fetch_with_retry",
        _fake_fetch_with_retry,
    )

    response = await client.get(
        "https://example.com/jobs",
        follow_redirects=True,
        params={"page": "1"},
    )

    assert response.status_code == 200
    assert captured["http"] is fake
    assert captured["url"] == "https://example.com/jobs"
    assert captured["follow_redirects"] is True
    assert captured["max_attempts"] == 3
    assert captured["kwargs"] == {"params": {"page": "1"}}


@pytest.mark.asyncio
async def test_retrying_client_retry_after_flows_through_tenacity_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    too_many = _fake_response(429)
    too_many.headers = {"Retry-After": "2"}
    ok = _fake_response(200)
    fake = _FakeAsyncClient([too_many, ok])
    client = _RetryingHttpClient(fake, max_retries=2, retry_delay_seconds=0.0)
    sleep = AsyncMock()

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.http_retry.sleep_with_source_deadline", sleep
    )

    response = await client.get("https://example.com/jobs")

    assert response.status_code == 200
    assert fake.calls == 2
    assert any(call.args[0] >= 2.0 for call in sleep.await_args_list)


def test_browser_utils_default_user_agent_is_chrome_131_or_newer() -> None:
    import re

    match = re.search(r"Chrome/(\d+)\.", browser_utils.DEFAULT_USER_AGENT)
    assert match is not None
    assert int(match.group(1)) >= 131


class _FakePage:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def evaluate(self, script: str, arg: str) -> dict[str, Any]:
        self.calls.append((script, arg))
        return self._result


@dataclass
class _FakeHtmlResponse:
    """Minimal stand-in for httpx.Response with headers + text for empty-200 tests."""

    status_code: int
    _text: str = ""
    _content_type: str = "text/html; charset=utf-8"
    request: httpx.Request | None = field(default=None)

    def __post_init__(self):
        if self.request is None:
            self.request = httpx.Request("GET", "https://example.com/jobs")

    @property
    def text(self) -> str:
        return self._text

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": self._content_type}

    def raise_for_status(self) -> None:
        pass  # 200 responses never raise


def test_is_empty_200_response_true_for_empty_html_body() -> None:
    r = _FakeHtmlResponse(status_code=200, _text="")
    assert _is_empty_200_response(r) is True  # type: ignore[arg-type]


def test_is_empty_200_response_true_for_whitespace_only_body() -> None:
    r = _FakeHtmlResponse(status_code=200, _text="   \n\t  ")
    assert _is_empty_200_response(r) is True  # type: ignore[arg-type]


def test_is_empty_200_response_false_for_non_empty_body() -> None:
    r = _FakeHtmlResponse(status_code=200, _text="<html><body><h1>Jobs</h1></body></html>")
    assert _is_empty_200_response(r) is False  # type: ignore[arg-type]


def test_is_empty_200_response_false_for_non_html_content_type() -> None:
    r = _FakeHtmlResponse(status_code=200, _text="", _content_type="application/json")
    assert _is_empty_200_response(r) is False  # type: ignore[arg-type]


def test_is_empty_200_response_false_for_non_200_status() -> None:
    r = _FakeHtmlResponse(status_code=404, _text="")
    assert _is_empty_200_response(r) is False  # type: ignore[arg-type]


@dataclass
class _FakeHtmlAsyncClient:
    """Drives _RetryingHttpClient with canned _FakeHtmlResponse objects."""

    responses: list[Any]
    calls: int = field(default=0, init=False)

    async def __aenter__(self) -> _FakeHtmlAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def get(self, url: str, *, follow_redirects: bool = False) -> Any:
        del url, follow_redirects
        self.calls += 1
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_retrying_client_retries_empty_200_once_then_returns() -> None:
    first = _FakeHtmlResponse(status_code=200, _text="")
    second = _FakeHtmlResponse(status_code=200, _text="<html><body>Jobs</body></html>")
    fake = _FakeHtmlAsyncClient([first, second])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    response = await client.get("https://example.com/jobs")

    assert response._text == "<html><body>Jobs</body></html>"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_retrying_client_empty_200_retry_is_bounded_to_one_attempt() -> None:
    empty = _FakeHtmlResponse(status_code=200, _text="")
    fake = _FakeHtmlAsyncClient([empty, _FakeHtmlResponse(status_code=200, _text="")])
    client = _RetryingHttpClient(fake, max_retries=0, retry_delay_seconds=0.0)

    response = await client.get("https://example.com/jobs")

    # Returns the second (still empty) response without a third attempt.
    assert response._text == ""
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_fetch_in_page_returns_status_headers_and_text() -> None:
    page = _FakePage({"status": 200, "headers": {"content-type": "text/html"}, "text": "<html/>"})

    with patch(
        "job_ftch.infrastructure.network.ssrf_guard.socket.getaddrinfo",
        return_value=[(None, None, None, "", ("93.184.216.34", 80))],
    ):
        result = await browser_utils.fetch_in_page(page, "https://example.com/job/1")

    assert result == {
        "status": 200,
        "headers": {"content-type": "text/html"},
        "text": "<html/>",
    }
    assert page.calls[0][1] == "https://example.com/job/1"
    assert "fetch(url)" in page.calls[0][0]


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Fake transport that records calls but never makes real network connections."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        return httpx.Response(200, request=request)

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.0.1",
        # IPv6 literals — gethostbyname (IPv4-only) used to let these bypass.
        "::1",
        "fc00::1",
        "fe80::1",
        # IPv4-mapped IPv6 form of a loopback address.
        "::ffff:127.0.0.1",
    ],
)
def test_host_is_private_identifies_internal_addresses(host: str) -> None:
    assert _host_is_private(host) is True


@pytest.mark.parametrize(
    "host",
    ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"],
)
def test_host_is_private_passes_public_addresses(host: str) -> None:
    assert _host_is_private(host) is False


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_ipv6_loopback_literal() -> None:
    inner = _RecordingTransport()
    guard = SSRFGuardedTransport(inner, allowlist=())
    request = httpx.Request("GET", "http://[::1]/internal")

    with pytest.raises(httpx.LocalProtocolError, match="SSRF guard"):
        await guard.handle_async_request(request)

    assert inner.calls == []


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_private_host() -> None:
    inner = _RecordingTransport()
    guard = SSRFGuardedTransport(inner, allowlist=())
    request = httpx.Request("GET", "http://127.0.0.1/internal")

    with pytest.raises(httpx.LocalProtocolError, match="SSRF guard"):
        await guard.handle_async_request(request)

    assert inner.calls == []


@pytest.mark.asyncio
async def test_ssrf_guard_allows_public_host() -> None:
    inner = _RecordingTransport()
    guard = SSRFGuardedTransport(inner, allowlist=())
    # Use a real public IP to avoid DNS resolution in tests.
    request = httpx.Request("GET", "http://93.184.216.34/jobs")

    await guard.handle_async_request(request)

    assert len(inner.calls) == 1


@pytest.mark.asyncio
async def test_ssrf_guard_allowlist_exempts_private_host() -> None:
    inner = _RecordingTransport()
    guard = SSRFGuardedTransport(inner, allowlist=("localhost",))
    request = httpx.Request("GET", "http://localhost/health")

    await guard.handle_async_request(request)

    assert len(inner.calls) == 1
