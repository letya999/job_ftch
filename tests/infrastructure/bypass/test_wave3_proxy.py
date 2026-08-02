"""Wave 3: Proxy preparation - gateway format, cost tracking, session binding, geo, CAPTCHA proxy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from job_ftch.infrastructure.bypass.proxy_bypass import (
    GatewayProxyProvider,
    ProxyCostTracker,
    is_proxy_rescue_domain_allowed,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _settings_test_defaults(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JOB_FTCH_LLM_BACKEND", "heuristic")
    monkeypatch.setenv("JOB_FTCH_JOB_BACKEND", "sqlite")
    monkeypatch.setenv("JOB_FTCH_SEARCH_BACKEND", "sqlite")
    monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_ALLOW_DOMAINS", "")
    monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_DENY_DOMAINS", "")
    yield
    import job_ftch.config

    job_ftch.config.get_settings.cache_clear()


class TestGatewayProxyProvider:
    def test_brightdata_url_format(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://brd.superproxy.io:7777",
            user="brd-customer-C12345",
            password="pass123",
            default_country="us",
        )
        url = gw.get_proxy_url(domain="example.com")
        assert "brd-customer-C12345-country-us-session-" in url
        assert ":pass123@" in url
        assert "brd.superproxy.io:7777" in url

    def test_oxylabs_url_format(self) -> None:
        gw = GatewayProxyProvider(
            provider="oxylabs",
            gateway="http://pr.oxylabs.io:7777",
            user="myuser",
            password="mypass",
            default_country="de",
        )
        url = gw.get_proxy_url(domain="example.de")
        assert "customer-myuser-cc-de-sessid-" in url

    def test_smartproxy_url_format(self) -> None:
        gw = GatewayProxyProvider(
            provider="smartproxy",
            gateway="http://gate.smartproxy.com:7777",
            user="sp_user",
            password="sp_pass",
        )
        url = gw.get_proxy_url(domain="test.com", country="gb")
        assert "sp_user-cc-gb-sessid-" in url

    def test_sticky_session_same_domain(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
            sticky_ttl_seconds=600,
        )
        url1 = gw.get_proxy_url(domain="example.com")
        url2 = gw.get_proxy_url(domain="example.com")
        assert url1 == url2

    def test_different_domains_different_sessions(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
        )
        url1 = gw.get_proxy_url(domain="a.com")
        url2 = gw.get_proxy_url(domain="b.com")
        assert url1 != url2

    def test_session_rotation_changes_id(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
        )
        url1 = gw.get_proxy_url(domain="example.com")
        gw.rotate_session("example.com")
        url2 = gw.get_proxy_url(domain="example.com")
        assert url1 != url2

    def test_country_override(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
            default_country="us",
        )
        url = gw.get_proxy_url(domain="example.com", country="jp")
        assert "-country-jp-" in url

    def test_generic_provider_fallback(self) -> None:
        gw = GatewayProxyProvider(
            provider="unknown_provider",
            gateway="http://gate:7777",
            user="u",
            password="p",
        )
        url = gw.get_proxy_url(domain="test.com", country="fr")
        assert "-country-fr-session-" in url

    def test_active_sessions_tracking(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
            sticky_ttl_seconds=600,
        )
        gw.get_proxy_url(domain="a.com")
        gw.get_proxy_url(domain="b.com")
        assert len(gw.active_sessions) == 2
        assert "a.com" in gw.active_sessions
        assert "b.com" in gw.active_sessions

    def test_dataimpulse_url_format(self) -> None:
        gw = GatewayProxyProvider(
            provider="dataimpulse",
            gateway="http://gw.dataimpulse.com:823",
            user="login",
            password="pass",
            default_country="ru",
            sticky_ttl_seconds=1800,
        )
        url = gw.get_proxy_url(domain="career.habr.com")
        assert url.startswith("http://login__cr.ru;")
        assert ";sessid." in url
        assert ";sessttl.30:pass@gw.dataimpulse.com:823" in url

    def test_dataimpulse_sticky_session_same_domain(self) -> None:
        gw = GatewayProxyProvider(
            provider="dataimpulse",
            gateway="http://gw.dataimpulse.com:823",
            user="login",
            password="pass",
            default_country="ru",
        )
        url1 = gw.get_proxy_url(domain="careers.higgsfield.kz")
        url2 = gw.get_proxy_url(domain="careers.higgsfield.kz")
        assert url1 == url2


class TestProxyRescueDomainPolicy:
    def test_allowlist_accepts_subdomains(self) -> None:
        assert is_proxy_rescue_domain_allowed(
            "jobs.career.habr.com",
            allow_domains=("career.habr.com",),
        )

    def test_denylist_wins_over_allowlist(self) -> None:
        assert not is_proxy_rescue_domain_allowed(
            "rabota.sber.ru",
            allow_domains=("sber.ru",),
            deny_domains=("rabota.sber.ru",),
        )

    def test_wildcard_denylist(self) -> None:
        assert not is_proxy_rescue_domain_allowed(
            "bank.gov.ru",
            allow_domains=("gov.ru",),
            deny_domains=("*.gov.ru",),
        )


class TestProxyCostTracker:
    def test_record_and_total(self) -> None:
        tracker = ProxyCostTracker()
        tracker.record("a.com", 1024)
        tracker.record("b.com", 2048)
        assert tracker.total_bytes == 3072
        assert tracker.total_gb == pytest.approx(3072 / (1024**3), abs=1e-12)

    def test_budget_exhausted(self) -> None:
        tracker = ProxyCostTracker(gb_budget=0.001)
        assert not tracker.budget_exhausted
        tracker.record("a.com", 2_000_000)
        assert tracker.budget_exhausted

    def test_no_budget_never_exhausted(self) -> None:
        tracker = ProxyCostTracker(gb_budget=0.0)
        tracker.record("a.com", 999_999_999)
        assert not tracker.budget_exhausted

    def test_budget_remaining(self) -> None:
        tracker = ProxyCostTracker(gb_budget=1.0)
        assert tracker.budget_remaining_gb == pytest.approx(1.0, abs=0.01)
        tracker.record("a.com", 500 * 1024 * 1024)
        remaining = tracker.budget_remaining_gb
        assert remaining == pytest.approx(0.5117, abs=0.01)

    def test_top_domains(self) -> None:
        tracker = ProxyCostTracker()
        tracker.record("a.com", 100)
        tracker.record("b.com", 300)
        tracker.record("c.com", 200)
        top = tracker.top_domains(2)
        assert len(top) == 2
        assert top[0] == ("b.com", 300)
        assert top[1] == ("c.com", 200)

    def test_per_domain_accumulation(self) -> None:
        tracker = ProxyCostTracker()
        tracker.record("a.com", 100)
        tracker.record("a.com", 200)
        assert tracker.bytes_by_domain["a.com"] == 300


class TestSessionRouteBinding:
    def test_cookie_keyed_by_exit_ip_and_persona(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainEntry

        entry = DomainEntry(domain="example.com")
        entry.add_cookie("cf_clearance", "val1", exit_ip="1.2.3.4", persona_id="p01")
        entry.add_cookie("cf_clearance", "val2", exit_ip="5.6.7.8", persona_id="p02")

        cookies_p1 = entry.get_valid_cookies(exit_ip="1.2.3.4", persona_id="p01")
        assert cookies_p1 == {"cf_clearance": "val1"}

        cookies_p2 = entry.get_valid_cookies(exit_ip="5.6.7.8", persona_id="p02")
        assert cookies_p2 == {"cf_clearance": "val2"}

    def test_cookie_without_route_key_returns_all(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainEntry

        entry = DomainEntry(domain="example.com")
        entry.add_cookie("cf_clearance", "val1", exit_ip="1.2.3.4", persona_id="p01")
        entry.add_cookie("cf_bm", "val2", exit_ip="5.6.7.8", persona_id="p02")

        all_cookies = entry.get_valid_cookies()
        assert len(all_cookies) == 2

    def test_cookie_same_name_different_route_coexist(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainEntry

        entry = DomainEntry(domain="example.com")
        entry.add_cookie("cf_clearance", "new", exit_ip="1.2.3.4", persona_id="p01")
        entry.add_cookie("cf_clearance", "new", exit_ip="1.2.3.4", persona_id="p01")

        cookies = entry.get_valid_cookies(exit_ip="1.2.3.4", persona_id="p01")
        assert cookies == {"cf_clearance": "new"}

    def test_domain_intel_cache_cookie_with_route(self) -> None:
        from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence

        intel = DomainIntelligence(cache_path="/dev/null")
        intel.cache_cookie(
            "example.com",
            "cf_clearance",
            "token_abc",
            exit_ip="10.0.0.1",
            persona_id="p03",
        )
        result = intel.get_cookies(
            "example.com",
            exit_ip="10.0.0.1",
            persona_id="p03",
        )
        assert result == {"cf_clearance": "token_abc"}

        no_match = intel.get_cookies(
            "example.com",
            exit_ip="99.99.99.99",
            persona_id="p99",
        )
        assert no_match == {}


class TestStrictGeoBinding:
    def test_residential_bypass_strict_geo_rejects_no_country(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When strict_geo=True and no country is available, selection must fail."""
        monkeypatch.setenv("JOB_FTCH_PROXY_PROVIDER", "brightdata")
        monkeypatch.setenv("JOB_FTCH_PROXY_GATEWAY", "http://gate:7777")
        monkeypatch.setenv("JOB_FTCH_PROXY_USER", "u")
        monkeypatch.setenv("JOB_FTCH_PROXY_PASS", "p")
        monkeypatch.setenv("JOB_FTCH_PROXY_STRICT_GEO", "true")
        monkeypatch.setenv("JOB_FTCH_PROXY_COUNTRY_DEFAULT", "")

        import importlib

        import job_ftch.config

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()

        from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

        bypass = ResidentialProxyBypass()
        result = bypass._select_for_domain("example.com")
        assert result is None

    def test_residential_bypass_strict_geo_accepts_with_country(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When strict_geo=True and country IS available, selection succeeds."""
        monkeypatch.setenv("JOB_FTCH_PROXY_PROVIDER", "brightdata")
        monkeypatch.setenv("JOB_FTCH_PROXY_GATEWAY", "http://gate:7777")
        monkeypatch.setenv("JOB_FTCH_PROXY_USER", "u")
        monkeypatch.setenv("JOB_FTCH_PROXY_PASS", "p")
        monkeypatch.setenv("JOB_FTCH_PROXY_STRICT_GEO", "true")
        monkeypatch.setenv("JOB_FTCH_PROXY_COUNTRY_DEFAULT", "us")

        import importlib

        import job_ftch.config

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()

        from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

        bypass = ResidentialProxyBypass()
        result = bypass._select_for_domain("example.com")
        assert result is not None
        assert "-country-us-" in result.url

    def test_gateway_generates_country_default(self) -> None:
        gw = GatewayProxyProvider(
            provider="brightdata",
            gateway="http://gate:7777",
            user="u",
            password="p",
            default_country="",
        )
        url = gw.get_proxy_url(domain="test.com")
        assert "-country-us-" in url


class TestResidentialProxyRescueRouting:
    def test_gateway_is_available_to_bypass_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JOB_FTCH_PROXY_PROVIDER", "dataimpulse")
        monkeypatch.setenv("JOB_FTCH_PROXY_GATEWAY", "http://gw.dataimpulse.com:823")
        monkeypatch.setenv("JOB_FTCH_PROXY_USER", "login")
        monkeypatch.setenv("JOB_FTCH_PROXY_PASS", "pass")
        monkeypatch.setenv("JOB_FTCH_PROXY_COUNTRY_DEFAULT", "ru")
        monkeypatch.setenv("JOB_FTCH_PROXY_STICKY_TTL_SECONDS", "1800")
        monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_ALLOW_DOMAINS", "career.habr.com")

        import importlib

        import job_ftch.config
        import job_ftch.infrastructure.bypass.proxy_bypass as mod

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()
        mod._cost_tracker = None

        from job_ftch.infrastructure.bypass.context import BypassContext
        from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

        bypass = ResidentialProxyBypass()
        context = BypassContext(
            persona=SimpleNamespace(),
            preflight=SimpleNamespace(tier="direct", reason="test"),
            domain="career.habr.com",
            residential_proxy=bypass,
        )
        context.set_effective_route(tier="residential_proxy", network="residential_proxy")

        assert context.residential_proxy_available
        assert "__cr.ru;" in (context.current_proxy_url or "")
        assert bypass.get_proxy_for(domain="career.habr.com", country="RU") is not None

    def test_rescue_policy_skips_denied_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JOB_FTCH_PROXY_PROVIDER", "dataimpulse")
        monkeypatch.setenv("JOB_FTCH_PROXY_GATEWAY", "http://gw.dataimpulse.com:823")
        monkeypatch.setenv("JOB_FTCH_PROXY_USER", "login")
        monkeypatch.setenv("JOB_FTCH_PROXY_PASS", "pass")
        monkeypatch.setenv("JOB_FTCH_PROXY_COUNTRY_DEFAULT", "ru")
        monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_ALLOW_DOMAINS", "career.habr.com,tbank.ru")
        monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_DENY_DOMAINS", "tbank.ru,rabota.sber.ru")

        import importlib

        import job_ftch.config
        import job_ftch.infrastructure.bypass.proxy_bypass as mod

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()
        mod._cost_tracker = None

        from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

        bypass = ResidentialProxyBypass()
        kwargs = bypass.apply_browser_args({"_domain_hint": "tbank.ru"})
        assert "proxy" not in kwargs

    def test_browser_proxy_uses_split_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JOB_FTCH_PROXY_PROVIDER", "dataimpulse")
        monkeypatch.setenv("JOB_FTCH_PROXY_GATEWAY", "http://gw.dataimpulse.com:823")
        monkeypatch.setenv("JOB_FTCH_PROXY_USER", "login")
        monkeypatch.setenv("JOB_FTCH_PROXY_PASS", "pass")
        monkeypatch.setenv("JOB_FTCH_PROXY_COUNTRY_DEFAULT", "ru")
        monkeypatch.setenv("JOB_FTCH_PROXY_RESCUE_ALLOW_DOMAINS", "career.habr.com")

        import importlib

        import job_ftch.config
        import job_ftch.infrastructure.bypass.proxy_bypass as mod

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()
        mod._cost_tracker = None

        from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

        bypass = ResidentialProxyBypass()
        kwargs = bypass.apply_browser_args({"_domain_hint": "career.habr.com"})
        assert kwargs["proxy"]["server"] == "http://gw.dataimpulse.com:823"
        assert kwargs["proxy"]["username"].startswith("login__cr.ru;")
        assert kwargs["proxy"]["password"] == "pass"


class TestCaptchaProxyPassthrough:
    def test_base_provider_proxy_task_fields(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_providers import _BaseProvider

        provider = _BaseProvider(
            "key123",
            proxy_url="http://user:pass@gate.proxy.com:7777",
        )
        fields = provider._proxy_task_fields()
        assert fields["proxyAddress"] == "gate.proxy.com"
        assert fields["proxyPort"] == 7777
        assert fields["proxyLogin"] == "user"
        assert fields["proxyPassword"] == "pass"
        # CapSolver / AntiCaptcha require a lowercase proxyType.
        assert fields["proxyType"] == "http"

    def test_base_provider_no_proxy_empty_fields(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_providers import _BaseProvider

        provider = _BaseProvider("key123")
        fields = provider._proxy_task_fields()
        assert fields == {}

    def test_capsolver_uses_proxy_task_type(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_providers import CapSolverProvider

        provider = CapSolverProvider("key", proxy_url="http://gate:7777")
        assert provider.proxy_url == "http://gate:7777"

    def test_captcha_solver_accepts_proxy_url(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(
            provider="capsolver",
            api_key="test",
            proxy_url="http://proxy:8080",
        )
        assert solver._proxy_url == "http://proxy:8080"

    def test_captcha_solver_set_proxy_url(self) -> None:
        from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass

        solver = CaptchaSolverBypass(provider="browser_wait", api_key="")
        solver.set_proxy_url("http://new-proxy:9999")
        assert solver._proxy_url == "http://new-proxy:9999"


class TestCostTrackerSingleton:
    def test_get_cost_tracker_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import job_ftch.infrastructure.bypass.proxy_bypass as mod

        mod._cost_tracker = None
        monkeypatch.setenv("JOB_FTCH_PROXY_GB_BUDGET", "5.0")

        import importlib

        import job_ftch.config

        importlib.reload(job_ftch.config)
        job_ftch.config.get_settings.cache_clear()

        from job_ftch.infrastructure.bypass.proxy_bypass import get_cost_tracker

        t1 = get_cost_tracker()
        t2 = get_cost_tracker()
        assert t1 is t2
        mod._cost_tracker = None
