"""Provider-neutral proxy pool primitives."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.proxy_pool import (
    GatewayProxyEndpointFactory,
    ManagedProxyPool,
    ProxyProviderSpec,
    ProxyRouteRequest,
    domain_matches,
    is_domain_allowed,
    redact_proxy_url,
)


def test_domain_policy_exact_subdomain_and_wildcard_deny() -> None:
    assert domain_matches("jobs.career.habr.com", "career.habr.com")
    assert is_domain_allowed(
        "career.habr.com",
        allow_domains=("career.habr.com",),
        deny_domains=("*.gov.ru",),
    )
    assert not is_domain_allowed(
        "foo.gov.ru",
        allow_domains=("gov.ru",),
        deny_domains=("*.gov.ru",),
    )


def test_dataimpulse_endpoint_format_and_stickiness() -> None:
    factory = GatewayProxyEndpointFactory(
        ProxyProviderSpec(
            name="dataimpulse",
            gateway="http://gw.dataimpulse.com:823",
            user="login",
            password="pass",  # pragma: allowlist secret
            default_country="RU",
            sticky_ttl_seconds=1800,
        )
    )

    first = factory.endpoint_for(ProxyRouteRequest(domain="career.habr.com"))
    second = factory.endpoint_for(ProxyRouteRequest(domain="career.habr.com"))

    assert first is not None
    assert second is not None
    assert first.url == second.url
    assert first.url.startswith("http://login__cr.ru;")
    assert ";sessid." in first.url
    assert ";sessttl.30:pass@gw.dataimpulse.com:823" in first.url


def test_managed_proxy_pool_selects_from_multiple_providers() -> None:
    dataimpulse = GatewayProxyEndpointFactory(
        ProxyProviderSpec(
            name="dataimpulse",
            gateway="http://gw.dataimpulse.com:823",
            user="di",
            password="secret",  # pragma: allowlist secret
            default_country="RU",
            allow_domains=("career.habr.com",),
        )
    )
    brightdata = GatewayProxyEndpointFactory(
        ProxyProviderSpec(
            name="brightdata",
            gateway="http://brd.superproxy.io:7777",
            user="brd-user",
            password="secret",  # pragma: allowlist secret
            default_country="US",
            allow_domains=("example.com",),
        )
    )
    pool = ManagedProxyPool(
        providers=[dataimpulse, brightdata],
        deny_domains=("tbank.ru",),
    )

    habr = pool.select(ProxyRouteRequest(domain="career.habr.com"))
    example = pool.select(ProxyRouteRequest(domain="example.com"))
    denied = pool.select(ProxyRouteRequest(domain="tbank.ru"))

    assert habr is not None
    assert habr.provider == "dataimpulse"
    assert example is not None
    assert example.provider == "brightdata"
    assert denied is None
    assert pool.stats().as_dict()["provider_names"] == ["dataimpulse", "brightdata"]


def test_proxy_endpoint_redaction_and_playwright_projection() -> None:
    factory = GatewayProxyEndpointFactory(
        ProxyProviderSpec(
            name="smartproxy",
            gateway="http://gate.smartproxy.com:7000",
            user="user",
            password="pass",  # pragma: allowlist secret
            default_country="DE",
        )
    )
    endpoint = factory.endpoint_for(ProxyRouteRequest(domain="example.de"))

    assert endpoint is not None
    assert redact_proxy_url(endpoint.url) == "http://***:***@gate.smartproxy.com:7000"
    assert endpoint.playwright_proxy() == {
        "server": "http://gate.smartproxy.com:7000",
        "username": endpoint.playwright_proxy()["username"],
        "password": "pass",  # pragma: allowlist secret
    }
