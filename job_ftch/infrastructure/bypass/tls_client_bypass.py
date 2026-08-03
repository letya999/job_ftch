from __future__ import annotations

import asyncio
from typing import Any

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.config import get_settings

try:
    import tls_client

    _TLS_CLIENT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    tls_client = None
    _TLS_CLIENT_AVAILABLE = False


def _supported_tls_identifiers() -> frozenset[str]:
    """Best-effort read of tls_client's supported client identifiers.

    tls_client enumerates identifiers in ``tls_client.settings``. Reading it
    lets a stale pin self-heal instead of raising at request time.
    """
    try:
        import typing

        from tls_client.settings import ClientIdentifiers

        return frozenset(str(arg) for arg in typing.get_args(ClientIdentifiers))
    except Exception:  # pragma: no cover - optional dependency / version drift
        return frozenset()


_SUPPORTED_TLS_IDENTIFIERS: frozenset[str] = _supported_tls_identifiers()


def _coerce_tls_identifier(identifier: str, fallback: str) -> str:
    if not _SUPPORTED_TLS_IDENTIFIERS:
        return identifier
    if identifier in _SUPPORTED_TLS_IDENTIFIERS:
        return identifier
    return fallback if fallback in _SUPPORTED_TLS_IDENTIFIERS else identifier


# Chrome-only pool (underscore form): this transport tier always carries a
# Chromium persona after the family fix (A5), so a Chrome JA3/JA4 stays
# coherent with the Chrome UA/headers. A mixed-family pool would reintroduce
# the "Chrome headers, Firefox TLS" mismatch (A11).
_CLIENT_IDENTIFIER_POOL: tuple[str, ...] = tuple(
    dict.fromkeys(
        _coerce_tls_identifier(identifier, "chrome_133")
        for identifier in ("chrome_133", "chrome_131", "chrome_120")
    )
)


def _select_client_identifier(url: str, default: str) -> str:
    if default and default != "auto":
        return default
    import hashlib
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.lower()
    if not domain:
        return _CLIENT_IDENTIFIER_POOL[0]
    idx = int(hashlib.sha256(domain.encode()).hexdigest(), 16) % len(_CLIENT_IDENTIFIER_POOL)
    return _CLIENT_IDENTIFIER_POOL[idx]


class _TlsClientResponse:
    """Small response adapter matching the httpx/curl surface used by scrapers."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.status_code = int(getattr(raw, "status_code", 0) or 0)
        self.status = self.status_code
        self.headers = dict(getattr(raw, "headers", {}) or {})
        self.text = str(getattr(raw, "text", "") or "")
        self.content = getattr(raw, "content", None) or self.text.encode("utf-8", errors="ignore")
        self.url = str(getattr(raw, "url", "") or "")

    def json(self) -> Any:
        import json

        raw_json = getattr(self._raw, "json", None)
        if callable(raw_json):
            return raw_json()
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        import httpx

        request = httpx.Request("GET", self.url or "https://invalid.local/")
        response = httpx.Response(
            self.status_code,
            headers=self.headers,
            content=self.content,
            request=request,
        )
        raise httpx.HTTPStatusError(
            f"HTTP status {self.status_code}",
            request=request,
            response=response,
        )


class TlsClientBypass:
    """HTTP transport backed by Python-TLS-Client.

    The upstream client is synchronous, so this adapter isolates calls in a
    worker thread and keeps the async ingest loop responsive. It is intentionally
    a transport tier, not a CAPTCHA or browser tier.
    """

    def __init__(
        self,
        *,
        client_identifier: str = "auto",
        random_tls_extension_order: bool = True,
    ) -> None:
        self.client_identifier = client_identifier
        self.random_tls_extension_order = random_tls_extension_order
        self._proxy_url: str | None = None

    def set_proxy_url(self, proxy_url: str | None) -> None:
        self._proxy_url = proxy_url

    async def apply_http(self, client: Any) -> Any:
        if tls_client is None:
            raise ImportError(
                "tls-client bypass requires the 'tls_client' extra: "
                "pip install job-ftch[tls_client]"
            )

        timeout = _resolve_timeout_seconds(client)
        proxy_url = self._proxy_url

        class TlsClientAdapter:
            def __init__(self, bypass: TlsClientBypass) -> None:
                self._bypass = bypass
                self._sessions: dict[str, Any] = {}

            def _get_session(self, url: str) -> Any:
                identifier = _select_client_identifier(url, self._bypass.client_identifier)
                session = self._sessions.get(identifier)
                if session is None:
                    session = tls_client.Session(
                        client_identifier=identifier,
                        random_tls_extension_order=self._bypass.random_tls_extension_order,
                    )
                    self._sessions[identifier] = session
                return session

            async def __aenter__(self) -> TlsClientAdapter:
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                self._sessions.clear()

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                from job_ftch.infrastructure.sources.ssrf_guard import check_ssrf

                await check_ssrf(url)
                return await self._request("get", url, follow_redirects, kwargs)

            async def post(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                from job_ftch.infrastructure.sources.ssrf_guard import check_ssrf

                await check_ssrf(url)
                return await self._request("post", url, follow_redirects, kwargs)

            async def _request(
                self,
                method: str,
                url: str,
                follow_redirects: bool,
                kwargs: dict[str, Any],
            ) -> _TlsClientResponse:
                session = self._get_session(url)
                call = getattr(session, method)
                request_kwargs = dict(kwargs)
                request_kwargs.setdefault("timeout_seconds", timeout)
                request_kwargs.setdefault("allow_redirects", follow_redirects)
                if proxy_url:
                    request_kwargs.setdefault("proxy", proxy_url)
                raw = await asyncio.to_thread(call, url, **request_kwargs)
                return _TlsClientResponse(raw)

        return TlsClientAdapter(self)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        del page


def _create_tls_client(bypass_config: dict[str, Any] | None = None) -> TlsClientBypass:
    config = bypass_config or {}
    return TlsClientBypass(
        client_identifier=str(config.get("client_identifier", "auto")),
        random_tls_extension_order=bool(config.get("random_tls_extension_order", True)),
    )


def _resolve_timeout_seconds(client: Any) -> float:
    timeout = getattr(client, "timeout", None)
    if isinstance(timeout, (int, float)) and timeout > 0:
        return float(timeout)
    if timeout is not None:
        for attr in ("read", "connect", "write", "pool"):
            value = getattr(timeout, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    settings = get_settings()
    return max(settings.monitor_timeout_seconds, 0.1)


if _TLS_CLIENT_AVAILABLE:
    register_bypass(
        "tls_client",
        capability=BypassCapability(
            cost=12,
            transport="tls_client",
            challenge_actions=frozenset({"tls_impersonation"}),
        ),
    )(_create_tls_client)
