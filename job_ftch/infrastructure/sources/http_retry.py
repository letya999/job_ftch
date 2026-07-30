"""Shared HTTP retry helper built on tenacity.

Provides a single ``fetch_with_retry`` coroutine that wraps httpx GET calls
with exponential-jitter backoff, Retry-After header respect, and structlog
tracing. Falls back to a single attempt when tenacity is not installed.

Usage::

    from job_ftch.infrastructure.sources.http_retry import fetch_with_retry

    resp = await fetch_with_retry(http, url)
    resp.raise_for_status()
    html = resp.text
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.infrastructure.sources.source_deadline import (
    await_with_source_deadline,
    remaining_source_seconds,
    sleep_with_source_deadline,
)

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger(__name__)
_RNG = random.SystemRandom()

# Status codes that are transient and warrant a retry.
_RETRY_STATUSES = {429, 500, 502, 503, 504}

_MAX_RETRY_AFTER_S = 30.0


def _safe_url_ref(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


try:
    from tenacity import (
        AsyncRetrying,
        RetryError,
        stop_after_attempt,
        wait_none,
    )

    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False


def parse_retry_after(
    header: str | None,
    *,
    now: datetime | None = None,
    cap_seconds: float = _MAX_RETRY_AFTER_S,
) -> float | None:
    """Parse delta-seconds or HTTP-date Retry-After into a bounded delay."""
    if not header:
        return None
    try:
        seconds = float(header.strip())
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(header.strip())
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        seconds = (retry_at.astimezone(UTC) - current.astimezone(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(0.0, seconds), max(0.0, cap_seconds))


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse Retry-After header; returns seconds or None."""
    header = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    return parse_retry_after(header)


async def _request(
    http: httpx.AsyncClient,
    url: str,
    *,
    method: str,
    follow_redirects: bool,
    **kwargs: Any,
) -> httpx.Response:
    """Perform exactly one request; route policy owns protection transitions."""
    request = getattr(http, method.lower())
    return cast(
        "httpx.Response",
        await request(url, follow_redirects=follow_redirects, **kwargs),
    )


def bounded_retry_delay(seconds: float) -> float:
    """Cap a retry wait to 30 seconds and 20% of the remaining source budget."""
    delay = min(max(0.0, seconds), _MAX_RETRY_AFTER_S)
    remaining = remaining_source_seconds()
    if remaining is not None:
        delay = min(delay, max(0.0, remaining * 0.2))
    return delay


async def fetch_with_retry(
    http: httpx.AsyncClient,
    url: str,
    *,
    follow_redirects: bool = True,
    max_attempts: int = 3,
    method: str = "GET",
    replay_safe: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """GET ``url`` with exponential-jitter retry on transient errors.

    Retries on network exceptions (TransportError, TimeoutException) and on
    HTTP status codes in ``{429, 500, 502, 503, 504}``. Respects Retry-After
    headers. Protection responses are returned immediately so the route
    controller can act on their body/header evidence without a redundant retry.

    Returns the final response; caller is responsible for ``raise_for_status``.
    Falls back to a single attempt when tenacity is not installed.
    """
    normalized_method = method.upper()
    if normalized_method not in {"GET", "HEAD"} and not replay_safe:
        max_attempts = 1
    else:
        # RouteBudget: one initial call plus at most one unchanged-route retry.
        max_attempts = min(max_attempts, 2)

    if not _TENACITY_AVAILABLE or max_attempts <= 1:
        return await await_with_source_deadline(
            _request(
                http,
                url,
                method=normalized_method,
                follow_redirects=follow_redirects,
                **kwargs,
            )
        )

    import httpx as _httpx

    last_resp: _httpx.Response | None = None
    attempt = 0

    try:
        async for attempt_ctx in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_none(),
        ):
            with attempt_ctx:
                attempt += 1
                try:
                    resp: _httpx.Response = await await_with_source_deadline(
                        _request(
                            http,
                            url,
                            method=normalized_method,
                            follow_redirects=follow_redirects,
                            **kwargs,
                        )
                    )
                except (_httpx.TransportError, _httpx.TimeoutException) as exc:
                    logger.warning(
                        "http_retry.transport_error",
                        url_hash=_safe_url_ref(url),
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt < max_attempts:
                        await sleep_with_source_deadline(
                            bounded_retry_delay(exponential_backoff_s(attempt, cap=8.0))
                        )
                    raise

                if resp.status_code in _RETRY_STATUSES:
                    # A route gets at most one status retry. Higher max_attempts
                    # remain available only for transport failures.
                    if attempt >= min(max_attempts, 2):
                        return resp
                    wait_s = _retry_after_seconds(resp)
                    if resp.status_code == 429:
                        from urllib.parse import urlparse

                        from job_ftch.infrastructure.bypass.pacing import get_domain_pacer

                        domain = urlparse(url).netloc.lower()
                        get_domain_pacer().record_rate_limit(domain, wait_s)
                    delay = bounded_retry_delay(
                        wait_s if wait_s is not None else exponential_backoff_s(attempt, cap=8.0)
                    )
                    if wait_s is not None:
                        logger.info(
                            "http_retry.retry_after",
                            url_hash=_safe_url_ref(url),
                            attempt=attempt,
                            status=resp.status_code,
                            wait_s=delay,
                        )
                    else:
                        logger.warning(
                            "http_retry.transient_status",
                            url_hash=_safe_url_ref(url),
                            attempt=attempt,
                            status=resp.status_code,
                        )
                    await sleep_with_source_deadline(delay)
                    last_resp = resp
                    # Raise to trigger tenacity retry; the exception message
                    # carries the status for log context.
                    raise _httpx.HTTPStatusError(
                        f"retryable {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                return resp

    except RetryError:
        # All attempts exhausted; return whatever we have so caller decides.
        if last_resp is not None:
            logger.error(
                "http_retry.exhausted",
                url_hash=_safe_url_ref(url),
                attempts=attempt,
                final_status=last_resp.status_code,
            )
            return last_resp
        raise

    # Unreachable; satisfies mypy.
    raise RuntimeError("fetch_with_retry: unexpected exit")


def jitter_sleep_s(base: float, spread: float = 1.0) -> float:
    """Return a jittered sleep duration (base ± uniform spread)."""
    return max(0.0, base + _RNG.uniform(-spread / 2, spread / 2))


def exponential_backoff_s(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Return capped exponential backoff in seconds for the given attempt (1-indexed)."""
    return min(cap, base * math.pow(2, attempt - 1))
