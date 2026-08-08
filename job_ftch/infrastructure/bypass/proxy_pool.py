"""Provider-neutral proxy routing primitives for bypass adapters.

This module deliberately stops at proxy selection and endpoint formatting.  It
does not know about CareerSiteSource, monitors, pipeline nodes, stores, or
Playwright/httpx clients; those integrations live in the surrounding adapters.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Protocol
from urllib.parse import urlparse


def normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).netloc.lower()
    raw = raw.rsplit("@", 1)[-1]
    raw = raw.split(":", 1)[0]
    return raw.strip(".")


def domain_matches(domain: str, pattern: str) -> bool:
    normalized_domain = normalize_domain(domain)
    normalized_pattern = normalize_domain(pattern)
    if not normalized_domain or not normalized_pattern:
        return False
    if "*" in normalized_pattern:
        return fnmatchcase(normalized_domain, normalized_pattern)
    if normalized_pattern.startswith("."):
        suffix = normalized_pattern.lstrip(".")
        return normalized_domain == suffix or normalized_domain.endswith(f".{suffix}")
    return normalized_domain == normalized_pattern or normalized_domain.endswith(
        f".{normalized_pattern}"
    )


def is_domain_allowed(
    domain: str,
    *,
    allow_domains: tuple[str, ...] = (),
    deny_domains: tuple[str, ...] = (),
) -> bool:
    normalized = normalize_domain(domain)
    if not normalized:
        return not allow_domains
    if any(domain_matches(normalized, pattern) for pattern in deny_domains):
        return False
    if not allow_domains:
        return True
    return any(domain_matches(normalized, pattern) for pattern in allow_domains)


def redact_proxy_url(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if not parsed.netloc:
        return proxy_url
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username:
        return f"{parsed.scheme or 'http'}://***:***@{host}{port}"
    return f"{parsed.scheme or 'http'}://{host}{port}"


@dataclass(frozen=True, slots=True)
class ProxyRouteRequest:
    domain: str
    country: str = ""
    session_key: str = ""
    purpose: str = "ingest"

    @property
    def normalized_domain(self) -> str:
        return normalize_domain(self.domain)


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """A selected proxy endpoint plus provider metadata."""

    url: str
    provider: str = "raw"
    endpoint_id: str = ""
    country: str = ""
    residential: bool = False
    sticky: bool = False

    @property
    def redacted_url(self) -> str:
        return redact_proxy_url(self.url)

    def playwright_proxy(self) -> dict[str, str]:
        parsed = urlparse(self.url)
        host = parsed.hostname or parsed.netloc or self.url
        scheme = parsed.scheme or "http"
        port = f":{parsed.port}" if parsed.port else ""
        proxy: dict[str, str] = {"server": f"{scheme}://{host}{port}"}
        if parsed.username:
            proxy["username"] = parsed.username
        if parsed.password:
            proxy["password"] = parsed.password
        return proxy


@dataclass(frozen=True, slots=True)
class ProxyProviderSpec:
    """Provider configuration with secrets already resolved by composition."""

    name: str
    gateway: str
    user: str = ""
    password: str = ""
    default_country: str = ""
    sticky_ttl_seconds: int = 600
    weight: float = 1.0
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    residential: bool = True


class ProxyProvider(Protocol):
    name: str
    weight: float

    def can_route(self, request: ProxyRouteRequest) -> bool: ...

    def endpoint_for(self, request: ProxyRouteRequest) -> ProxyEndpoint | None: ...

    def rotate(self, domain: str) -> None: ...


class GatewayProxyEndpointFactory:
    """Build gateway proxy URLs for managed residential providers."""

    _PROVIDER_TEMPLATES: dict[str, str] = {
        "brightdata": "{user}-country-{country}-session-{session}",
        "oxylabs": "customer-{user}-cc-{country}-sessid-{session}",
        "smartproxy": "{user}-cc-{country}-sessid-{session}",
        "generic": "{user}-country-{country}-session-{session}",
    }

    def __init__(self, spec: ProxyProviderSpec) -> None:
        self.spec = spec
        self.name = spec.name.lower()
        self.weight = max(spec.weight, 0.0)
        self._domain_sessions: dict[str, tuple[str, float]] = {}

    def _ttl_minutes(self) -> int:
        return max(1, round(self.spec.sticky_ttl_seconds / 60))

    def _session_id(self, domain: str) -> str:
        now = time.monotonic()
        if domain in self._domain_sessions:
            session_id, created_at = self._domain_sessions[domain]
            if (now - created_at) < self.spec.sticky_ttl_seconds:
                return session_id
        session_id = uuid.uuid4().hex[:12]
        self._domain_sessions[domain] = (session_id, now)
        return session_id

    def can_route(self, request: ProxyRouteRequest) -> bool:
        return is_domain_allowed(
            request.normalized_domain,
            allow_domains=self.spec.allow_domains,
            deny_domains=self.spec.deny_domains,
        )

    def _username(self, *, country: str, session_id: str) -> str:
        provider = self.name
        if provider == "dataimpulse":
            return (
                f"{self.spec.user}__cr.{country};sessid.{session_id};sessttl.{self._ttl_minutes()}"
            )
        template = self._PROVIDER_TEMPLATES.get(
            provider,
            self._PROVIDER_TEMPLATES["generic"],
        )
        return template.format(user=self.spec.user, country=country, session=session_id)

    def endpoint_for(self, request: ProxyRouteRequest) -> ProxyEndpoint | None:
        if not self.can_route(request):
            return None
        parsed = urlparse(self.spec.gateway)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or self.spec.gateway
        port = parsed.port or 7777
        country = (request.country or self.spec.default_country or "us").lower()
        session_key = request.session_key or request.normalized_domain
        session_id = self._session_id(session_key) if session_key else uuid.uuid4().hex[:12]
        username = self._username(country=country, session_id=session_id)
        return ProxyEndpoint(
            url=f"{scheme}://{username}:{self.spec.password}@{host}:{port}",
            provider=self.name,
            endpoint_id=f"{self.name}:{host}:{port}:{session_id}",
            country=country.upper(),
            residential=self.spec.residential,
            sticky=True,
        )

    def rotate(self, domain: str) -> None:
        normalized = normalize_domain(domain)
        self._domain_sessions.pop(normalized, None)

    @property
    def active_sessions(self) -> dict[str, str]:
        now = time.monotonic()
        return {
            domain: sid
            for domain, (sid, created_at) in self._domain_sessions.items()
            if (now - created_at) < self.spec.sticky_ttl_seconds
        }


@dataclass(slots=True)
class ProxyPoolStats:
    active_static: int = 0
    active_providers: int = 0
    pruned_static: int = 0
    provider_names: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_static": self.active_static,
            "active_providers": self.active_providers,
            "pruned_static": self.pruned_static,
            "provider_names": list(self.provider_names),
        }


@dataclass(slots=True)
class ManagedProxyPool:
    """Weighted static+gateway proxy pool with domain-sticky selection."""

    static_endpoints: list[ProxyEndpoint] = field(default_factory=list)
    providers: list[ProxyProvider] = field(default_factory=list)
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    _domain_pins: dict[str, ProxyEndpoint] = field(default_factory=dict)
    _pruned_static: list[ProxyEndpoint] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.static_endpoints or self.providers)

    def _pool_allowed(self, request: ProxyRouteRequest) -> bool:
        return is_domain_allowed(
            request.normalized_domain,
            allow_domains=self.allow_domains,
            deny_domains=self.deny_domains,
        )

    def _weighted_provider(self, request: ProxyRouteRequest) -> ProxyProvider | None:
        candidates = [provider for provider in self.providers if provider.can_route(request)]
        if not candidates:
            return None
        weights = [max(provider.weight, 0.05) for provider in candidates]
        total = sum(weights)
        marker = random.uniform(0, total)
        cumulative = 0.0
        for provider, weight in zip(candidates, weights, strict=False):
            cumulative += weight
            if marker <= cumulative:
                return provider
        return candidates[-1]

    def select(self, request: ProxyRouteRequest) -> ProxyEndpoint | None:
        if not self._pool_allowed(request):
            return None
        domain = request.normalized_domain
        if domain in self._domain_pins:
            return self._domain_pins[domain]

        provider = self._weighted_provider(request)
        endpoint = provider.endpoint_for(request) if provider else None
        if endpoint is None and self.static_endpoints:
            endpoint = random.choice(self.static_endpoints)
        if endpoint and domain:
            self._domain_pins[domain] = endpoint
        return endpoint

    def rotate(self, domain: str) -> None:
        normalized = normalize_domain(domain)
        self._domain_pins.pop(normalized, None)
        for provider in self.providers:
            provider.rotate(normalized)

    def stats(self) -> ProxyPoolStats:
        return ProxyPoolStats(
            active_static=len(self.static_endpoints),
            active_providers=len(self.providers),
            pruned_static=len(self._pruned_static),
            provider_names=tuple(provider.name for provider in self.providers),
        )
