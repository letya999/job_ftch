"""Proxy-rotation bypass tier with health scoring, auto-pruning, and geo-routing.

Reads proxy URLs from JOB_FTCH_PROXY_LIST (comma-separated) and/or
config/proxies.yaml. On every apply_http / apply_browser_args call, uses
the current proxy from the rotation. On handle_failure, advances to next
proxy and updates health score.

Includes proxy IP verification (pattern from Botasaurus ip_utils.py):
`verify_proxy()` checks that the proxy actually changes the real IP,
and `get_public_ip()` fetches the current public IP through the proxy.

Health scoring: each proxy tracks success/failure counts with an EMA
(exponential moving average). Proxies below the prune threshold are
automatically removed from the rotation. Selection is weighted by health
score so healthier proxies get more traffic.

Geo-routing: optional proxy geo-detection via free ipapi.co endpoint.
Proxies can be tagged with country codes for geo-aware selection.

Gateway format: residential providers (BrightData, Oxylabs, SmartProxy)
use a gateway URL with session/country encoded in the username:
``http://user-country-us-session-abc123:pass@gate.provider.com:7777``.

Cost accounting: tracks bytes transferred per domain for GB budget caps.

Tor integration: when JOB_FTCH_TOR_SOCKS5 is set, Tor is added to the
pool as a SOCKS5 proxy with automatic circuit renewal on failure.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.infrastructure.bypass.proxy_pool import (
    GatewayProxyEndpointFactory,
    ProxyEndpoint,
    ProxyProviderSpec,
    ProxyRouteRequest,
    redact_proxy_url,
)
from job_ftch.infrastructure.bypass.proxy_pool import (
    is_domain_allowed as is_proxy_rescue_domain_allowed,
)
from job_ftch.infrastructure.bypass.proxy_pool import (
    normalize_domain as _normalize_domain,
)

logger = structlog.get_logger("job_ftch.bypass.proxy")

_IP_ENDPOINTS = ("https://api.ipify.org", "https://checkip.amazonaws.com")
_GEO_ENDPOINT = "https://ipapi.co/json/"
CAPSOLVER_CHALLENGE_PROXY_ENV = "JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST"
_TOR_CONTROL_DEFAULT = "127.0.0.1:9051"
_TOR_SOCKS5_DEFAULT = "socks5://127.0.0.1:9050"


@dataclass(slots=True)
class ProxyHealth:
    """Per-proxy health tracking with EMA scoring."""

    url: str
    successes: int = 0
    failures: int = 0
    ema_score: float = 1.0
    country: str | None = None
    last_used: float = 0.0
    last_failure_at: float = 0.0
    consecutive_failures: int = 0

    _ALPHA: float = 0.3

    def record_success(self) -> None:
        self.successes += 1
        self.consecutive_failures = 0
        self.ema_score += self._ALPHA * (1.0 - self.ema_score)
        self.last_used = time.monotonic()

    def record_failure(self) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.ema_score += self._ALPHA * (0.0 - self.ema_score)
        self.last_used = time.monotonic()
        self.last_failure_at = time.monotonic()

    @property
    def total_attempts(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 1.0
        return self.successes / self.total_attempts


def _load_proxies() -> list[str]:
    proxies: list[str] = []
    yaml_path = Path(__file__).parents[3] / "config" / "proxies.yaml"
    if yaml_path.exists():
        import yaml

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        proxies.extend(p for p in data.get("proxies", []) if p)
    env_val = os.environ.get("JOB_FTCH_PROXY_LIST", "")
    proxies.extend(p.strip() for p in env_val.split(",") if p.strip())
    tor_url = os.environ.get("JOB_FTCH_TOR_SOCKS5", "").strip()
    if tor_url:
        proxies.append(tor_url)
    elif os.environ.get("JOB_FTCH_TOR_ENABLED", "").lower() in ("1", "true", "yes"):
        proxies.append(_TOR_SOCKS5_DEFAULT)
    return list(dict.fromkeys(proxies))


def _normalize_proxy_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if "://" not in url and ":" in url:
        return f"http://{url}"
    return url


def _select_weighted(pool: list[ProxyHealth]) -> ProxyHealth | None:
    if not pool:
        return None
    weights = [max(h.ema_score, 0.05) for h in pool]
    total = sum(weights)
    if total <= 0:
        return random.choice(pool)
    r = random.uniform(0, total)
    cumulative = 0.0
    for health, weight in zip(pool, weights, strict=False):
        cumulative += weight
        if r <= cumulative:
            return health
    return pool[-1]


@dataclass(slots=True)
class ProxyCostTracker:
    """Per-domain byte accounting with optional GB budget cap.

    Wave 3.2 additions:
    - ``per_domain_gb_budget``: cap per individual domain (default 0 = unlimited).
    - ``domain_budget_exhausted(domain)``: check if a single domain hit its cap.
    - ``job_count`` / ``cost_per_job_gb``: track how many jobs were produced
      to compute cost-per-job for operator dashboards.
    """

    bytes_by_domain: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0
    gb_budget: float = 0.0
    per_domain_gb_budget: float = 0.0
    job_count: int = 0

    def record(self, domain: str, byte_count: int) -> None:
        self.bytes_by_domain[domain] = self.bytes_by_domain.get(domain, 0) + byte_count
        self.total_bytes += byte_count
        if self.budget_exhausted:
            logger.warning(
                "proxy_global_budget_exhausted",
                total_gb=round(self.total_gb, 4),
                budget_gb=self.gb_budget,
            )
        if self.domain_budget_exhausted(domain):
            logger.warning(
                "proxy_domain_budget_exhausted",
                domain=domain,
                domain_gb=round(self.domain_gb(domain), 4),
                budget_gb=self.per_domain_gb_budget,
            )

    def record_job(self, count: int = 1) -> None:
        """Increment produced-job counter for cost-per-job calculation."""
        self.job_count += count

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)

    @property
    def budget_exhausted(self) -> bool:
        return self.gb_budget > 0 and self.total_gb >= self.gb_budget

    @property
    def budget_remaining_gb(self) -> float:
        if self.gb_budget <= 0:
            return float("inf")
        return max(0.0, self.gb_budget - self.total_gb)

    def domain_gb(self, domain: str) -> float:
        return self.bytes_by_domain.get(domain, 0) / (1024**3)

    def domain_budget_exhausted(self, domain: str) -> bool:
        """Check if a specific domain exceeded its per-domain budget."""
        if self.per_domain_gb_budget <= 0:
            return False
        return self.domain_gb(domain) >= self.per_domain_gb_budget

    def should_allow_request(self, domain: str) -> bool:
        """Hard-stop gate: return False if any budget cap is hit."""
        return not (self.budget_exhausted or self.domain_budget_exhausted(domain))

    @property
    def cost_per_job_gb(self) -> float:
        """Average GB spent per produced job (0 if no jobs yet)."""
        if self.job_count <= 0:
            return 0.0
        return self.total_gb / self.job_count

    def top_domains(self, n: int = 10) -> list[tuple[str, int]]:
        return sorted(self.bytes_by_domain.items(), key=lambda x: x[1], reverse=True)[:n]


class GatewayProxyProvider:
    """Generate gateway-format proxy URLs for residential providers.

    Providers like BrightData, Oxylabs, SmartProxy use a single gateway
    endpoint with session/country encoded in the username:
    ``http://user-country-us-session-abc123:pass@gate.provider.com:7777``
    """

    def __init__(
        self,
        *,
        provider: str,
        gateway: str,
        user: str,
        password: str,
        default_country: str = "",
        sticky_ttl_seconds: int = 600,
    ) -> None:
        self.provider = provider.lower()
        self.gateway = gateway
        self.user = user
        self.password = password
        self.default_country = default_country.upper()
        self.sticky_ttl_seconds = sticky_ttl_seconds
        self._factory = GatewayProxyEndpointFactory(
            ProxyProviderSpec(
                name=self.provider,
                gateway=self.gateway,
                user=self.user,
                password=self.password,
                default_country=self.default_country,
                sticky_ttl_seconds=self.sticky_ttl_seconds,
            )
        )

    def _ttl_minutes(self) -> int:
        return self._factory._ttl_minutes()

    def _session_id(self, domain: str) -> str:
        return self._factory._session_id(_normalize_domain(domain))

    def get_proxy_url(self, *, domain: str = "", country: str = "") -> str:
        endpoint = self._factory.endpoint_for(ProxyRouteRequest(domain=domain, country=country))
        return endpoint.url if endpoint else ""

    def rotate_session(self, domain: str) -> str:
        self._factory.rotate(domain)
        return self._session_id(domain)

    @property
    def active_sessions(self) -> dict[str, str]:
        return self._factory.active_sessions


_cost_tracker: ProxyCostTracker | None = None


def get_cost_tracker() -> ProxyCostTracker:
    global _cost_tracker
    if _cost_tracker is None:
        from job_ftch.config import get_settings

        settings = get_settings()
        _cost_tracker = ProxyCostTracker(
            gb_budget=getattr(settings, "proxy_gb_budget", 0.0),
            per_domain_gb_budget=getattr(settings, "proxy_per_domain_gb_budget", 0.0),
        )
    return _cost_tracker


class ProxyBypass:
    """Health-aware proxy rotation with auto-pruning and geo-routing.

    Proxies are selected via weighted random sampling based on EMA health
    scores. Failed proxies are automatically pruned when their score drops
    below the threshold. Tor proxies get automatic circuit renewal on failure.
    """

    PRUNE_THRESHOLD: float = 0.1
    PRUNE_MIN_ATTEMPTS: int = 5
    MAX_CONSECUTIVE_FAILURES: int = 3

    def __init__(self) -> None:
        raw = _load_proxies()
        self._health_pool: list[ProxyHealth] = [
            ProxyHealth(url=_normalize_proxy_url(p)) for p in raw if _normalize_proxy_url(p)
        ]
        self._current: ProxyHealth | None = _select_weighted(self._health_pool)
        self._pruned: list[str] = []
        self._geo_cache: dict[str, str] = {}

    @property
    def _proxies(self) -> list[str]:
        return [h.url for h in self._health_pool]

    @property
    def current_url(self) -> str | None:
        return self._current.url if self._current else None

    def _prune_dead(self) -> int:
        pruned_count = 0
        survivors: list[ProxyHealth] = []
        for h in self._health_pool:
            if h.total_attempts >= self.PRUNE_MIN_ATTEMPTS and h.ema_score < self.PRUNE_THRESHOLD:
                logger.warning(
                    "proxy_pruned",
                    url=redact_proxy_url(h.url),
                    score=h.ema_score,
                    attempts=h.total_attempts,
                )
                self._pruned.append(h.url)
                pruned_count += 1
            elif h.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "proxy_pruned_consecutive",
                    url=redact_proxy_url(h.url),
                    streak=h.consecutive_failures,
                )
                self._pruned.append(h.url)
                pruned_count += 1
            else:
                survivors.append(h)
        self._health_pool = survivors
        return pruned_count

    def _rotate(self) -> None:
        self._prune_dead()
        self._current = _select_weighted(self._health_pool)
        if self._current:
            logger.info(
                "proxy_rotated",
                proxy=redact_proxy_url(self._current.url),
                score=self._current.ema_score,
            )

    async def apply_http(self, client: Any) -> Any:
        current = self._current
        if not current:
            return client
        proxy_url = current.url
        timeout_seconds = getattr(client, "timeout", None)

        class ProxyHttpxAdapter:
            def __init__(self, proxy: str, health: ProxyHealth) -> None:
                self.proxy = proxy
                self._health = health
                from job_ftch.infrastructure.network.ssrf_guard import SSRFGuardedTransport

                transport = httpx.AsyncHTTPTransport(proxy=proxy)
                self._client = httpx.AsyncClient(
                    timeout=timeout_seconds, transport=SSRFGuardedTransport(transport)
                )

            async def __aenter__(self) -> ProxyHttpxAdapter:
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                await self._client.aclose()

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                try:
                    resp = await self._client.get(url, follow_redirects=follow_redirects, **kwargs)
                    self._health.record_success()
                    return resp
                except Exception:
                    self._health.record_failure()
                    raise

            async def post(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                try:
                    resp = await self._client.post(url, follow_redirects=follow_redirects, **kwargs)
                    self._health.record_success()
                    return resp
                except Exception:
                    self._health.record_failure()
                    raise

        return ProxyHttpxAdapter(proxy_url, current)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.current_url:
            kwargs["proxy"] = ProxyEndpoint(url=self.current_url).playwright_proxy()
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass

    def handle_failure(
        self,
        url: str,
        *,
        status_code: int | None,
        body: bytes | None,
        error: Exception | None,
    ) -> None:
        del body, error
        if self._current:
            logger.warning(
                "proxy_failure",
                url=url,
                proxy=redact_proxy_url(self._current.url),
                status=status_code,
                score=self._current.ema_score,
            )
            self._current.record_failure()
        self._rotate()

    def handle_success(self, url: str) -> None:
        if self._current:
            self._current.record_success()

    async def detect_geo(self, proxy_url: str, *, timeout: float = 10.0) -> str | None:
        """Detect proxy country via ipapi.co. Cached per proxy URL."""
        if proxy_url in self._geo_cache:
            return self._geo_cache[proxy_url]
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
                resp = await client.get(_GEO_ENDPOINT)
                resp.raise_for_status()
                data = resp.json()
                country = data.get("country_code", "").upper() or None
                if country:
                    self._geo_cache[proxy_url] = country
                    for h in self._health_pool:
                        if h.url == proxy_url:
                            h.country = country
                return country
        except Exception as exc:
            logger.debug(
                "proxy_geo_detect_failed", proxy=redact_proxy_url(proxy_url), error=str(exc)
            )
            return None

    async def detect_geo_all(self, *, timeout: float = 10.0) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        for h in self._health_pool:
            results[h.url] = await self.detect_geo(h.url, timeout=timeout)
        return results

    async def verify_current(
        self,
        *,
        timeout: float = 10.0,
        retries: int = 3,
    ) -> dict[str, Any] | None:
        if not self._current:
            return None
        return await verify_proxy(self._current.url, timeout=timeout, retries=retries)

    async def verify_all(
        self,
        *,
        timeout: float = 10.0,
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        results = []
        for h in self._health_pool:
            result = await verify_proxy(h.url, timeout=timeout, retries=retries)
            if result and result.get("verified"):
                h.record_success()
            elif result:
                h.record_failure()
            results.append(
                {"proxy": redact_proxy_url(h.url), **(result or {"error": "verification failed"})}
            )
        self._prune_dead()
        return results

    def select_for_country(self, country_code: str) -> str | None:
        """Select a proxy tagged with a specific country code."""
        candidates = [
            h for h in self._health_pool if h.country and h.country.upper() == country_code.upper()
        ]
        chosen = _select_weighted(candidates)
        return chosen.url if chosen else None

    @property
    def pool_stats(self) -> dict[str, Any]:
        return {
            "active": len(self._health_pool),
            "pruned": len(self._pruned),
            "avg_score": (
                sum(h.ema_score for h in self._health_pool) / len(self._health_pool)
                if self._health_pool
                else 0.0
            ),
            "current": redact_proxy_url(self.current_url) if self.current_url else None,
        }


async def get_public_ip(
    *,
    proxy: str | None = None,
    timeout: float = 10.0,
    retries: int = 3,
) -> str | None:
    """Fetch the current public IP through the given proxy (or direct).

    Tries each endpoint in _IP_ENDPOINTS with retry on failure.
    Returns the IP string or None on failure.

    Pattern from Botasaurus ip_utils.py _find_ip().
    """
    for attempt in range(retries):
        for url in _IP_ENDPOINTS:
            try:
                async with httpx.AsyncClient(
                    proxy=proxy,
                    timeout=timeout,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    ip = resp.text.strip()
                    if ip:
                        return ip
            except (httpx.HTTPError, httpx.TimeoutException):
                continue
        # all endpoints failed for this attempt — small backoff
        if attempt < retries - 1:
            import asyncio

            await asyncio.sleep(1.0 * (attempt + 1))
    return None


async def verify_proxy(
    proxy: str,
    *,
    timeout: float = 10.0,
    retries: int = 3,
) -> dict[str, Any] | None:
    """Verify that a proxy changes the public IP.

    Returns a dict with keys:
        - "direct_ip": the IP without proxy
        - "proxy_ip": the IP through the proxy
        - "verified": True if proxy_ip != direct_ip
        - "error": error message if verification failed

    Returns None if the check itself could not complete.
    """
    result: dict[str, Any] = {
        "direct_ip": None,
        "proxy_ip": None,
        "verified": False,
        "error": None,
    }

    # Step 1: get direct IP (no proxy)
    direct_ip = await get_public_ip(proxy=None, timeout=timeout, retries=retries)
    result["direct_ip"] = direct_ip

    # Step 2: get IP through proxy
    proxy_ip = await get_public_ip(proxy=proxy, timeout=timeout, retries=retries)
    result["proxy_ip"] = proxy_ip

    if direct_ip is None or proxy_ip is None:
        result["error"] = "could not determine one or both IPs"
        return result

    result["verified"] = proxy_ip != direct_ip
    if not result["verified"]:
        result["error"] = f"proxy did not change IP: {proxy_ip} (same as direct)"
    return result


# ---------------------------------------------------------------------------
# Residential proxy tier: domain-sticky sessions with geo-pinning
# ---------------------------------------------------------------------------


def _load_residential_proxies() -> list[str]:
    proxies: list[str] = []
    proxies.extend(_load_capsolver_cloudflare_proxies())
    yaml_path = Path(__file__).parents[3] / "config" / "proxies.yaml"
    if yaml_path.exists():
        import yaml

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        proxies.extend(p for p in data.get("residential", []) if p)
    env_val = os.environ.get("JOB_FTCH_RESIDENTIAL_PROXY_LIST", "")
    proxies.extend(p.strip() for p in env_val.split(",") if p.strip())
    return list(dict.fromkeys(proxies))


def _load_capsolver_cloudflare_proxies() -> list[str]:
    """Operator-provided static/sticky proxies for CapSolver Cloudflare tasks.

    These are prepended to the residential pool so the browser route and
    CapSolver's ``AntiCloudflareTask`` use the same compatible endpoint.
    """
    env_val = os.environ.get(CAPSOLVER_CHALLENGE_PROXY_ENV, "")
    return [p.strip() for p in env_val.split(",") if p.strip()]


class ResidentialProxyBypass(ProxyBypass):
    """Residential proxy pool with domain-sticky sessions and geo-pinning.

    Supports two modes:
    - **Raw URLs**: reads from ``residential`` key in proxies.yaml or
      ``JOB_FTCH_RESIDENTIAL_PROXY_LIST`` env var.
    - **Gateway format**: when ``proxy_provider`` is set in config, generates
      gateway URLs on-the-fly with sticky session IDs per domain.

    Session stickiness: pins the same proxy IP per domain. Gateway mode
    rotates session IDs automatically via TTL.

    Strict geo-binding: when ``proxy_strict_geo`` is enabled, refuses to
    serve a proxy if the exit country is unknown.

    Cost accounting: tracks bytes per domain through the shared cost tracker.
    """

    def __init__(self, bypass_config: dict[str, Any] | None = None) -> None:
        raw = _load_residential_proxies()
        self._health_pool: list[ProxyHealth] = [
            ProxyHealth(url=_normalize_proxy_url(p)) for p in raw if _normalize_proxy_url(p)
        ]
        self._current: ProxyHealth | None = _select_weighted(self._health_pool)
        self._pruned: list[str] = []
        self._geo_cache: dict[str, str] = {}
        self._domain_pin: dict[str, ProxyHealth] = {}
        self._config = bypass_config or {}
        geo = self._config.get("proxy_geo")
        if isinstance(geo, str) and geo:
            self._preferred_geo: str | None = geo.upper()
        else:
            self._preferred_geo = None

        from job_ftch.config import get_settings

        settings = get_settings()
        self._gateway: GatewayProxyProvider | None = None
        self._strict_geo = bool(getattr(settings, "proxy_strict_geo", False))
        self._rescue_allow_domains = tuple(getattr(settings, "proxy_rescue_allow_domains", ()))
        self._rescue_deny_domains = tuple(getattr(settings, "proxy_rescue_deny_domains", ()))
        provider = getattr(settings, "proxy_provider", "raw")
        gateway = getattr(settings, "proxy_gateway", "")
        if provider not in ("raw", "") and gateway:
            self._gateway = GatewayProxyProvider(
                provider=provider,
                gateway=gateway,
                user=getattr(settings, "proxy_user", ""),
                password=getattr(settings, "proxy_pass", ""),
                default_country=getattr(settings, "proxy_country_default", "")
                or (self._preferred_geo or ""),
                sticky_ttl_seconds=getattr(settings, "proxy_sticky_ttl_seconds", 600),
            )
        self._cost = get_cost_tracker()

    @property
    def available(self) -> bool:
        return self._gateway is not None or bool(self._health_pool)

    def current_url_for_domain(self, domain: str) -> str | None:
        current = self._resolve_current(domain)
        return current.url if current else None

    def get_proxy_for(
        self,
        *,
        domain: str,
        country: str = "",
        purpose: str = "ingest",
    ) -> str | None:
        del purpose
        current = self._select_for_domain_with_country(domain, country=country)
        return current.url if current else None

    def rotate_proxy_for(self, domain: str) -> None:
        if self._gateway is not None:
            self._gateway.rotate_session(domain)
        self._domain_pin.pop(domain, None)
        self._domain_pin.pop(_normalize_domain(domain), None)

    def proxy_stats(self) -> dict[str, Any]:
        return self.cost_stats

    def _domain_allowed(self, domain: str) -> bool:
        return is_proxy_rescue_domain_allowed(
            domain,
            allow_domains=self._rescue_allow_domains,
            deny_domains=self._rescue_deny_domains,
        )

    def _get_proxy_url_for_domain(
        self,
        domain: str,
        *,
        country: str = "",
    ) -> str | None:
        """Return a proxy URL for the domain, preferring gateway mode."""
        if not self._domain_allowed(domain):
            logger.info("residential_proxy_domain_not_allowed", domain=_normalize_domain(domain))
            return None
        if self._gateway is not None:
            effective_country = (
                country or self._preferred_geo or self._gateway.default_country or ""
            )
            if self._strict_geo and not effective_country:
                logger.warning(
                    "proxy_strict_geo_rejected",
                    domain=domain,
                    reason="no country available for strict geo-binding",
                )
                return None
            return self._gateway.get_proxy_url(
                domain=domain,
                country=effective_country,
            )
        return None

    def _select_for_domain(self, domain: str) -> ProxyHealth | None:
        """Return a sticky proxy for the domain, selecting one if needed."""
        if self._gateway is not None:
            url = self._get_proxy_url_for_domain(domain or "")
            if url:
                return ProxyHealth(url=url)
            if self._strict_geo:
                return None

        if domain in self._domain_pin:
            pinned = self._domain_pin[domain]
            if pinned in self._health_pool:
                return pinned
            del self._domain_pin[domain]
        if self._preferred_geo:
            candidates = [
                h
                for h in self._health_pool
                if h.country and h.country.upper() == self._preferred_geo
            ]
            if self._strict_geo and not candidates:
                logger.warning(
                    "proxy_strict_geo_no_candidate",
                    domain=domain,
                    preferred_geo=self._preferred_geo,
                )
                return None
            chosen = _select_weighted(candidates) if candidates else None
        else:
            chosen = None
        if chosen is None:
            chosen = _select_weighted(self._health_pool)
        if chosen:
            self._domain_pin[domain] = chosen
        return chosen

    def _select_for_domain_with_country(
        self,
        domain: str,
        *,
        country: str = "",
    ) -> ProxyHealth | None:
        if self._gateway is not None:
            url = self._get_proxy_url_for_domain(domain or "", country=country)
            if url:
                return ProxyHealth(url=url)
            if self._strict_geo:
                return None
        return self._select_for_domain(domain)

    def _resolve_current(self, domain: str | None) -> ProxyHealth | None:
        """Pick the active proxy for a request.

        Gateway mode has no static pool, so it must always route through
        ``_select_for_domain`` (an empty ``domain`` yields an ephemeral
        session rather than leaking a direct connection). Raw-URL mode keeps
        its historical behaviour: sticky per domain, otherwise the rotation's
        current pick.
        """
        if self._gateway is not None:
            return self._select_for_domain(domain or "")
        if domain:
            return self._select_for_domain(domain)
        return self._current

    async def apply_http(self, client: Any) -> Any:
        domain = getattr(client, "_domain_hint", None)
        if domain and not self._domain_allowed(domain):
            return client
        if domain and not self._cost.should_allow_request(domain):
            return client
        elif not domain and self._cost.budget_exhausted:
            logger.warning("proxy_budget_exhausted", total_gb=f"{self._cost.total_gb:.3f}")
            return client

        timeout_seconds = getattr(client, "timeout", None)
        current = self._resolve_current(domain)

        if not current:
            if self._strict_geo:
                raise RuntimeError(
                    f"Strict geo-binding enforced, but no suitable proxy found for domain {domain}"
                )
            return client

        cost_tracker = self._cost

        class ResidentialHttpxAdapter:
            def __init__(self, proxy: str, health: ProxyHealth) -> None:
                self.proxy = proxy
                self._health = health
                from job_ftch.infrastructure.network.ssrf_guard import SSRFGuardedTransport

                transport = httpx.AsyncHTTPTransport(proxy=proxy)
                self._client = httpx.AsyncClient(
                    timeout=timeout_seconds, transport=SSRFGuardedTransport(transport)
                )
                self._domain_hint: str | None = None

            async def __aenter__(self) -> ResidentialHttpxAdapter:
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                await self._client.aclose()

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                try:
                    resp = await self._client.get(url, follow_redirects=follow_redirects, **kwargs)
                    self._health.record_success()
                    _track_response_bytes(resp, url, cost_tracker)
                    return resp
                except Exception:
                    self._health.record_failure()
                    raise

            async def post(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                try:
                    resp = await self._client.post(url, follow_redirects=follow_redirects, **kwargs)
                    self._health.record_success()
                    _track_response_bytes(resp, url, cost_tracker)
                    return resp
                except Exception:
                    self._health.record_failure()
                    raise

        return ResidentialHttpxAdapter(current.url, current)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        domain = kwargs.pop("_domain_hint", None)
        if domain and not self._domain_allowed(domain):
            return kwargs
        if (domain and not self._cost.should_allow_request(domain)) or (
            not domain and self._cost.budget_exhausted
        ):
            return kwargs

        current = self._resolve_current(domain)
        if current:
            kwargs["proxy"] = ProxyEndpoint(url=current.url).playwright_proxy()
        elif self._strict_geo:
            raise RuntimeError(
                f"Strict geo-binding enforced, but no suitable proxy found for domain {domain}"
            )
        return kwargs

    def handle_failure(
        self,
        url: str,
        *,
        status_code: int | None,
        body: bytes | None,
        error: Exception | None,
    ) -> None:
        domain = urlparse(url).netloc.lower()
        if self._gateway is not None:
            self._gateway.rotate_session(domain)
            logger.info("gateway_session_rotated", domain=domain, status=status_code)
            return
        pinned = self._domain_pin.get(domain)
        if pinned:
            logger.warning(
                "residential_proxy_failure",
                url=url,
                proxy=redact_proxy_url(pinned.url),
                status=status_code,
                score=pinned.ema_score,
            )
            pinned.record_failure()
            if pinned.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                del self._domain_pin[domain]
                self._prune_dead()
        elif self._current:
            self._current.record_failure()
            self._rotate()

    @property
    def gateway_provider(self) -> GatewayProxyProvider | None:
        return self._gateway

    @property
    def cost_stats(self) -> dict[str, Any]:
        return {
            "total_gb": round(self._cost.total_gb, 4),
            "budget_gb": self._cost.gb_budget,
            "budget_remaining_gb": round(self._cost.budget_remaining_gb, 4),
            "budget_exhausted": self._cost.budget_exhausted,
            "top_domains": self._cost.top_domains(5),
        }


def _track_response_bytes(
    resp: Any,
    url: str,
    tracker: ProxyCostTracker,
) -> None:
    try:
        content_length = int(getattr(resp, "headers", {}).get("content-length", 0))
        if content_length <= 0:
            content_length = len(getattr(resp, "content", b"") or b"")
        if content_length > 0:
            domain = urlparse(url).netloc.lower()
            tracker.record(domain, content_length)
    except (ValueError, TypeError):
        pass


if _load_residential_proxies():
    register_bypass(
        "residential_proxy",
        capability=BypassCapability(cost=8, transport="proxy", supports_proxy=True),
    )(lambda bypass_config=None: ResidentialProxyBypass(bypass_config))


def _has_gateway_config() -> bool:
    try:
        from job_ftch.config import get_settings

        settings = get_settings()
        return settings.proxy_provider not in ("raw", "") and bool(settings.proxy_gateway)
    except Exception:
        return False


if not _load_residential_proxies() and _has_gateway_config():
    register_bypass(
        "residential_proxy",
        capability=BypassCapability(cost=8, transport="proxy", supports_proxy=True),
    )(lambda bypass_config=None: ResidentialProxyBypass(bypass_config))
