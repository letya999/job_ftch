"""Tests for browser/bypass capability inventory and route planner diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.browser_capability_inventory import (
    build_browser_capability_inventory,
    explain_route_plan,
    explanation_to_public_dict,
    inventory_to_public_dict,
)
from job_ftch.application.registry import BypassCapability
from job_ftch.config import Settings

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _settings(**overrides: Any) -> Settings:
    base = {
        "browser_headless": True,
        "browser_session_state_enabled": False,
        "browser_profile_persistent": False,
        "http_proxy_list": [],
        "proxy_gateway": "",
        "proxy_user": "",
        "proxy_pass": "",
        "proxy_provider": "raw",
        "captcha_provider": "nopecha",
        "captcha_enabled_providers": ["browser_wait", "nopecha"],
        "career_site_browser_concurrency": 3,
        "career_site_timeout_seconds": 15.0,
        "browser_default_timeout_ms": 30000,
        "browser_context_timeout_ms": 120000,
        "captcha_solver_timeout_budget_seconds": 40.0,
    }
    base.update(overrides)
    return Settings(**base)


def _registered() -> dict[str, BypassCapability]:
    return {
        "noop": BypassCapability(cost=0, transport="httpx"),
        "curl_stealth": BypassCapability(cost=10, transport="curl_impersonation"),
        "stealth_browser": BypassCapability(
            cost=20,
            transport="browser",
            browser_family="chromium",
            supports_proxy=True,
        ),
        "nodriver": BypassCapability(
            cost=30,
            transport="browser",
            browser_family="chromium_cdp",
            owns_session=True,
            legal_gate="adr_073",
        ),
        "residential_proxy": BypassCapability(cost=8, transport="proxy", supports_proxy=True),
        "captcha_solver": BypassCapability(
            cost=50,
            transport="browser",
            browser_family="decorator",
            challenge_actions=frozenset({"captcha"}),
        ),
    }


def test_inventory_includes_required_capability_groups() -> None:
    inventory = build_browser_capability_inventory(
        _settings(),
        registered=_registered(),
    )
    assert inventory.status == "ok"
    groups = {
        item.group
        for item in inventory.capabilities
        if item.id.startswith("group:")
    }
    assert groups == {
        "direct_http",
        "stealth_http_tls",
        "browser",
        "persistent_session",
        "proxy_backed",
        "manual_challenge",
        "disabled_unavailable",
    }


def test_direct_http_available_and_proxy_unavailable_without_secrets() -> None:
    inventory = build_browser_capability_inventory(
        _settings(),
        registered=_registered(),
    )
    by_id = {item.id: item for item in inventory.capabilities}

    direct = by_id["group:direct_http"]
    assert direct.availability == "available"
    assert direct.supports_js is False
    assert direct.cost == 0
    assert direct.risk == "low"

    proxy = by_id["group:proxy_backed"]
    assert proxy.availability == "unavailable"
    assert proxy.reason is not None
    assert "proxy" in proxy.reason.lower()
    assert all(not secret.present for secret in proxy.required_secrets)

    engine_proxy = by_id["engine:residential_proxy"]
    assert engine_proxy.availability == "unavailable"


def test_proxy_available_when_list_configured_without_exposing_urls() -> None:
    settings = _settings(http_proxy_list=["redacted-proxy-entry"])
    inventory = build_browser_capability_inventory(settings, registered=_registered())
    proxy = next(item for item in inventory.capabilities if item.id == "group:proxy_backed")
    assert proxy.availability == "available"
    assert any(secret.label == "http_proxy_list" and secret.present for secret in proxy.required_secrets)

    payload = inventory_to_public_dict(inventory)
    serialized = str(payload).lower()
    assert "redacted-proxy-entry" not in serialized


def test_missing_captcha_secret_is_degraded_or_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOPECHA_API_KEY", raising=False)
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    inventory = build_browser_capability_inventory(
        _settings(captcha_provider="nopecha", captcha_enabled_providers=["nopecha", "browser_wait"]),
        registered=_registered(),
    )
    challenge = next(
        item for item in inventory.capabilities if item.id == "group:manual_challenge"
    )
    assert challenge.availability in {"degraded", "unavailable"}
    labels = {item.label: item.present for item in challenge.required_secrets}
    assert labels.get("nopecha") is False
    assert labels.get("browser_wait") is True


def test_captcha_secret_present_marks_provider_available(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOPECHA_API_KEY", "not-a-real-secret-value")
    inventory = build_browser_capability_inventory(
        _settings(captcha_provider="nopecha", captcha_enabled_providers=["nopecha"]),
        registered=_registered(),
    )
    challenge = next(
        item for item in inventory.capabilities if item.id == "group:manual_challenge"
    )
    assert challenge.availability == "available"
    payload = inventory_to_public_dict(inventory)
    assert "not-a-real-secret-value" not in str(payload)


def test_persistent_session_disabled_by_default() -> None:
    inventory = build_browser_capability_inventory(
        _settings(browser_session_state_enabled=False, browser_profile_persistent=False),
        registered=_registered(),
    )
    session = next(
        item for item in inventory.capabilities if item.id == "group:persistent_session"
    )
    assert session.availability == "disabled"
    assert session.requires_approval is True


def test_persistent_session_available_when_enabled() -> None:
    inventory = build_browser_capability_inventory(
        _settings(browser_session_state_enabled=True),
        registered=_registered(),
    )
    session = next(
        item for item in inventory.capabilities if item.id == "group:persistent_session"
    )
    assert session.availability == "available"
    assert session.supports_session is True


def test_unregistered_engine_is_unavailable_with_reason() -> None:
    inventory = build_browser_capability_inventory(
        _settings(),
        registered={"noop": BypassCapability(cost=0)},
    )
    camoufox = next(
        item for item in inventory.capabilities if item.id == "engine:camoufox"
    )
    assert camoufox.availability == "unavailable"
    assert camoufox.reason is not None
    assert "not registered" in camoufox.reason


def test_route_planner_selects_direct_http_by_default() -> None:
    explanation = explain_route_plan(
        settings=_settings(),
        source={"source_id": "src-1", "kind": "career_site"},
        source_id="src-1",
        registered=_registered(),
    )
    assert explanation.error is None
    assert explanation.selected_capability_id == "engine:noop"
    assert explanation.selected_group == "direct_http"
    selected = [item for item in explanation.diagnostics if item.status == "selected"]
    assert len(selected) == 1
    assert selected[0].capability_id == "engine:noop"


def test_route_planner_honors_explicit_bypass_pin() -> None:
    explanation = explain_route_plan(
        settings=_settings(),
        source={"source_id": "src-1", "kind": "career_site", "bypass": "stealth_browser"},
        source_id="src-1",
        registered=_registered(),
    )
    assert explanation.selected_capability_id == "engine:stealth_browser"
    assert explanation.requested_bypass == "stealth_browser"
    selected = next(item for item in explanation.diagnostics if item.status == "selected")
    assert "explicit" in selected.reason


def test_route_planner_explains_unavailable_requested_bypass() -> None:
    explanation = explain_route_plan(
        settings=_settings(),
        source={"source_id": "src-1", "kind": "career_site"},
        source_id="src-1",
        requested_bypass="residential_proxy",
        registered=_registered(),
    )
    # Proxy requested but not configured -> unavailable, falls back.
    assert explanation.requested_bypass == "residential_proxy"
    unavailable = [
        item
        for item in explanation.diagnostics
        if item.engine == "residential_proxy" and item.status == "unavailable"
    ]
    assert unavailable
    assert explanation.selected_capability_id == "engine:noop"


def test_route_planner_uses_assessment_probe_blocked_hint() -> None:
    explanation = explain_route_plan(
        settings=_settings(),
        source={
            "source_id": "src-blocked",
            "kind": "career_site",
            "assessment": {
                "freshness": {"probe_blocked": True},
                "capabilities": {"has_embedded_state": True},
            },
        },
        source_id="src-blocked",
        registered=_registered(),
    )
    assert explanation.selected_capability_id == "engine:curl_stealth"
    assert explanation.selected_group == "stealth_http_tls"
    assert any("probe_blocked" in note for note in explanation.notes)


def test_public_payload_redacts_sensitive_snippets() -> None:
    inventory = build_browser_capability_inventory(
        _settings(http_proxy_list=["socks5://internal-proxy:1080"]),
        registered=_registered(),
    )
    payload = inventory_to_public_dict(inventory)
    blob = str(payload).lower()
    assert "socks5://" not in blob
    assert "internal-proxy" not in blob
    assert "password" not in blob or "required_secrets" in blob

    explanation = explain_route_plan(
        settings=_settings(),
        source={"source_id": "x", "kind": "career_site"},
        registered=_registered(),
    )
    exp_payload = explanation_to_public_dict(explanation)
    assert "cookie" not in str(exp_payload).lower() or "redacted" in str(exp_payload).lower()


def test_engine_entries_expose_public_safe_fields() -> None:
    inventory = build_browser_capability_inventory(
        _settings(),
        registered=_registered(),
    )
    browser = next(item for item in inventory.capabilities if item.id == "engine:stealth_browser")
    assert browser.supports_js is True
    assert browser.supports_proxy is True
    assert browser.hard_timeout_seconds == 30.0
    assert browser.max_concurrency == 3
    assert browser.description
    assert browser.requires_approval is True
    assert browser.risk == "high"
