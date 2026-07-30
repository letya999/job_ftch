"""Career-site source runtime and layout-specific parsers."""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx

from job_ftch.infrastructure.sources.source_deadline import sleep_with_source_deadline
from job_ftch.infrastructure.sources.ssrf_guard import SSRFGuardedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType


# Realistic Chrome-on-Windows UA. httpx has no default User-Agent that mimics
# a real browser, and a bare/absent UA is an instant bot-fingerprint signal
# that some career sites reject outright.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Real Chrome HTML-fetch Accept header. httpx's own default is ``*/*``, which
# some sites (e.g. Uber's career pages) reject with HTTP 406. Keeping
# ``*/*;q=0.8`` at the tail means non-HTML endpoints still match via the
# wildcard; per-request ``Accept`` overrides from monitor/scraper configs
# still win (httpx merges client + request headers, per-request wins on
# conflict).
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

# Statuses considered transient/retryable in addition to any 5xx: 408 (request
# timeout), 425 (too early), 429 (rate-limited).
_EXTRA_RETRYABLE_STATUSES = frozenset({408, 425, 429})

# Cold-session WAF statuses eligible for a single bounded retry on the SAME
# client. Some anti-bot layers 401/403 the first request of a session and
# then pass once a challenge cookie is set by the client itself (e.g. via
# a Set-Cookie header on the blocked response) — retrying once recovers
# those without masking a genuine, persistent hard block.
_SOFT_403_STATUSES = frozenset({401, 403})


def _make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context tolerant of CDNs that mishandle TLS internals.

    Some CDNs (notably Akamai) send TLS 1.3 session tickets that can hang
    httpcore's async I/O; ``OP_NO_TICKET`` disables session-ticket
    negotiation to avoid this, mirroring urllib3's default behavior.

    ``OP_LEGACY_SERVER_CONNECT`` allows connections to servers that require
    legacy TLS renegotiation, which OpenSSL 3.0+ disables by default. The
    constant is looked up via ``getattr`` because it may be absent on newer
    Python/OpenSSL builds.

    Uses certifi's CA bundle instead of the system store for broader
    coverage of intermediate CA certificates.

    Returns:
        A configured :class:`ssl.SSLContext` for use as httpx's ``verify``.
    """
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.options |= ssl.OP_NO_TICKET
    op_legacy_server_connect = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    ctx.options |= op_legacy_server_connect
    return ctx


class _RetryingHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    async def __aenter__(self) -> _RetryingHttpClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def aclose(self) -> None:
        """Release the underlying connection pool without entering it first."""
        await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        follow_redirects: bool = False,
        **request_kwargs: Any,
    ) -> httpx.Response:
        from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

        empty_200_used = False
        soft_403_used = False
        while True:
            retry_kwargs = dict(request_kwargs)
            if method != "GET":
                retry_kwargs["method"] = method
            response = await fetch_with_retry(
                self._client,
                url,
                follow_redirects=follow_redirects,
                max_attempts=self._max_retries + 1,
                **retry_kwargs,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # One cold-session 401/403 retry is retained for sites that
                # set a clearance cookie in the first response. It cannot
                # become the new unbounded 4xx retry loop.
                if _is_soft_403_error(exc) and not soft_403_used:
                    soft_403_used = True
                    await sleep_with_source_deadline(self._retry_delay_seconds)
                    continue
                raise
            if _is_empty_200_response(response) and not empty_200_used:
                empty_200_used = True
                await sleep_with_source_deadline(self._retry_delay_seconds)
                continue
            return response

    async def get(
        self,
        url: str,
        *,
        follow_redirects: bool = False,
        **request_kwargs: Any,
    ) -> httpx.Response:
        return await self._request_with_retry(
            "GET",
            url,
            follow_redirects=follow_redirects,
            **request_kwargs,
        )

    async def post(
        self,
        url: str,
        *,
        follow_redirects: bool = False,
        **request_kwargs: Any,
    ) -> httpx.Response:
        return await self._request_with_retry(
            "POST",
            url,
            follow_redirects=follow_redirects,
            **request_kwargs,
        )


@asynccontextmanager
async def _http_session(client: Any, *, own_client: bool) -> AsyncIterator[Any]:
    if own_client:
        async with client as managed_client:
            yield managed_client
        return
    yield client


@asynccontextmanager
async def client_for_config(
    client: Any, config: dict[str, Any] | None = None
) -> AsyncIterator[Any]:
    """Yield the right HTTP client for a monitor/scraper config.

    When ``skip_ssl`` is enabled for a specific monitor or scraper, create a
    fresh retrying client with TLS verification disabled for that scope only.
    Otherwise reuse the outer source client unchanged.
    """
    if config and config.get("skip_ssl"):
        async with build_default_http_client(verify_ssl=False) as insecure_client:
            yield insecure_client
        return
    yield client


def _is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in _EXTRA_RETRYABLE_STATUSES
    return False


def _is_soft_403_error(exc: Exception) -> bool:
    """Whether *exc* is a cold-session WAF status eligible for one soft retry.

    Args:
        exc: The exception raised by ``response.raise_for_status()``.

    Returns:
        ``True`` for HTTP 401/403 responses only; all other errors (including
        other 4xx and 5xx statuses) are handled by ``_is_retryable_http_error``.
    """
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _SOFT_403_STATUSES


def _is_empty_200_response(response: httpx.Response) -> bool:
    """Whether a 200 response has an empty HTML body — an anti-bot signal.

    Some WAF/CDN layers (DataDome, Cloudflare) return HTTP 200 with an empty
    or near-empty HTML body on the FIRST request of a cold session, then let
    the real page through on the retry once the session cookie is set.
    Only triggers for text/html content-type to avoid false-positives on JSON
    or binary endpoints that legitimately return small bodies.
    """
    if response.status_code != 200:
        return False
    ct = response.headers.get("content-type", "").lower()
    if "text/html" not in ct:
        return False
    return not response.text.strip()


def build_default_http_client(*, verify_ssl: bool = True) -> _RetryingHttpClient:
    """Build the default retrying HTTP client for career-site fetches.

    Args:
        verify_ssl: When ``True`` (default), TLS verification uses a
            hardened :class:`ssl.SSLContext` (certifi CA bundle,
            ``OP_NO_TICKET``, ``OP_LEGACY_SERVER_CONNECT``). When ``False``,
            TLS verification is disabled entirely for the ``skip_ssl`` scope,
            preserving the existing insecure-client path.

    Returns:
        A :class:`_RetryingHttpClient` wrapping an ``httpx.AsyncClient`` with
        a realistic Chrome UA/Accept header pair and the configured
        timeout/limits/retry settings.
    """
    from job_ftch.config import get_settings

    settings = get_settings()
    timeout = httpx.Timeout(
        settings.career_site_timeout_seconds,
        connect=settings.career_site_connect_timeout_seconds,
    )
    limits = httpx.Limits(
        max_keepalive_connections=settings.career_site_max_keepalive_connections,
        max_connections=settings.career_site_max_connections,
    )
    verify: bool | ssl.SSLContext = _make_ssl_context() if verify_ssl else False
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": DEFAULT_ACCEPT}
    inner_transport = httpx.AsyncHTTPTransport(verify=verify, limits=limits)
    transport = SSRFGuardedTransport(inner_transport)
    return _RetryingHttpClient(
        httpx.AsyncClient(timeout=timeout, headers=headers, transport=transport),
        max_retries=settings.career_site_max_retries,
        retry_delay_seconds=settings.career_site_retry_delay_seconds,
    )
