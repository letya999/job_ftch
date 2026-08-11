"""Build public-safe browser/bypass capability inventory and route diagnostics.

Reuses the registry capability map and settings budgets. Does not open browsers,
read cookies/tokens/proxy URLs, or execute routes. Secret-bearing state is
reported as redacted presence booleans and provider labels only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from job_ftch.application.registry import BypassCapability, list_bypass_capabilities
from job_ftch.config import Settings, get_settings
from job_ftch.domain.browser_capability_inventory import (
    BrowserCapabilityEntry,
    BrowserCapabilityInventory,
    CapabilityAvailability,
    CapabilityGroup,
    CapabilityRisk,
    RequiredSecretState,
    RouteCapabilityDiagnostic,
    RouteDiagnosticStatus,
    RoutePlanExplanation,
)

# Public-safe fallback order mirrors infrastructure adaptive policy without
# importing infrastructure modules into application.
DEFAULT_FALLBACK_ORDER: tuple[str, ...] = (
    "noop",
    "curl_stealth",
    "tls_client",
    "stealth_browser",
    "patchright_browser",
    "nodriver",
    "camoufox",
    "cloak",
)

_ENGINE_GROUP: dict[str, CapabilityGroup] = {
    "noop": "direct_http",
    "curl_stealth": "stealth_http_tls",
    "tls_client": "stealth_http_tls",
    "stealth_browser": "browser",
    "patchright_browser": "browser",
    "nodriver": "browser",
    "camoufox": "browser",
    "cloak": "browser",
    "residential_proxy": "proxy_backed",
    "proxy": "proxy_backed",
    "session_handoff": "persistent_session",
    "captcha_solver": "manual_challenge",
}

_ENGINE_DESCRIPTIONS: dict[str, str] = {
    "noop": "Direct HTTP via standard client without anti-bot impersonation.",
    "curl_stealth": "Stealth HTTP transport with TLS/HTTP impersonation.",
    "tls_client": "Alternative stealth HTTP transport with sticky TLS client identity.",
    "stealth_browser": "Playwright-compatible browser route for JS rendering.",
    "patchright_browser": "Patchright browser route for fingerprint-sensitive pages.",
    "nodriver": "Session-owning CDP browser route (license-gated).",
    "camoufox": "Firefox anti-detect browser route for heavy fingerprint cases.",
    "cloak": "Last-resort patched Chromium browser route.",
    "residential_proxy": "Proxy-backed network path for IP/rate-limit isolation.",
    "proxy": "Generic proxy-backed network path.",
    "session_handoff": "Persistent/handoff session route across transport changes.",
    "captcha_solver": "Challenge/CAPTCHA path with provider or passive wait.",
}

_GROUP_DESCRIPTIONS: dict[CapabilityGroup, str] = {
    "direct_http": "Plain HTTP fetch without browser or stealth transport.",
    "stealth_http_tls": "HTTP routes with TLS/HTTP fingerprint impersonation.",
    "browser": "Headless/headed browser engines for JS-rendered sources.",
    "persistent_session": "Warm browser/session state reuse across attempts.",
    "proxy_backed": "Network path that can exit through a configured proxy.",
    "manual_challenge": "Challenge handling that may require provider or operator help.",
    "disabled_unavailable": "Routes that are registered but currently unusable.",
}

# Env var names only — values are never returned or logged.
_CAPTCHA_PROVIDER_ENV: dict[str, str] = {
    "capsolver": "CAPSOLVER_API_KEY",
    "capmonster": "CAPMONSTER_API_KEY",
    "nextcaptcha": "NEXTCAPTCHA_API_KEY",
    "2captcha": "TWOCAPTCHA_API_KEY",
    "anticaptcha": "ANTICAPTCHA_API_KEY",
    "nopecha": "NOPECHA_API_KEY",
}

_SENSITIVE_SNIPPET_MARKERS = (
    "http://",
    "https://",
    "socks5://",
    "socks4://",
    "bearer ",
    "cookie=",
    "authorization:",
    "api_key",
    "token=",
    "password=",
    ".runtime/",
    "c:\\",
    "/home/",
    "/users/",
)


def _risk_for_cost(cost: int) -> CapabilityRisk:
    if cost >= 50:
        return "critical"
    if cost >= 20:
        return "high"
    if cost >= 10:
        return "medium"
    return "low"


def _env_secret_present(env_name: str) -> bool:
    """Return whether a process env secret is non-empty without exposing value."""
    return bool(os.environ.get(env_name, "").strip())


def _safe_reason(text: str | None, *, fallback: str = "unavailable") -> str:
    if not text:
        return fallback
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_SNIPPET_MARKERS):
        return "details redacted"
    cleaned = " ".join(text.split())
    return cleaned[:160] if cleaned else fallback


def _group_for_engine(name: str, capability: BypassCapability) -> CapabilityGroup:
    if name in _ENGINE_GROUP:
        return _ENGINE_GROUP[name]
    if capability.browser_family is not None:
        return "browser"
    if capability.transport in {"proxy", "residential_proxy"}:
        return "proxy_backed"
    if "session_handoff" in capability.challenge_actions or capability.owns_session:
        return "persistent_session"
    if any(action in capability.challenge_actions for action in ("captcha", "manual", "solver")):
        return "manual_challenge"
    if capability.transport in {"curl_impersonation", "tls_client", "curl_stealth"}:
        return "stealth_http_tls"
    return "direct_http"


def _proxy_secret_states(settings: Settings) -> tuple[RequiredSecretState, ...]:
    states = [
        RequiredSecretState(
            label="http_proxy_list",
            present=bool(settings.http_proxy_list),
        ),
        RequiredSecretState(
            label="proxy_gateway",
            present=bool(str(settings.proxy_gateway or "").strip()),
        ),
        RequiredSecretState(
            label="proxy_credentials",
            present=bool(str(settings.proxy_user or "").strip())
            and bool(str(settings.proxy_pass or "").strip()),
        ),
        RequiredSecretState(
            label="JOB_FTCH_PROXY_LIST",
            present=_env_secret_present("JOB_FTCH_PROXY_LIST"),
        ),
        RequiredSecretState(
            label="JOB_FTCH_RESIDENTIAL_PROXY_LIST",
            present=_env_secret_present("JOB_FTCH_RESIDENTIAL_PROXY_LIST"),
        ),
    ]
    return tuple(states)


def _proxy_configured(settings: Settings) -> bool:
    return any(item.present for item in _proxy_secret_states(settings))


def _captcha_secret_states(settings: Settings) -> tuple[RequiredSecretState, ...]:
    enabled = {str(name).strip().lower() for name in settings.captcha_enabled_providers}
    provider = str(settings.captcha_provider or "").strip().lower()
    labels = sorted(enabled | ({provider} if provider else set()) | {"browser_wait"})
    states: list[RequiredSecretState] = []
    for label in labels:
        if label in {"", "browser_wait"}:
            states.append(RequiredSecretState(label="browser_wait", present=True))
            continue
        env_name = _CAPTCHA_PROVIDER_ENV.get(label)
        if env_name is None:
            states.append(RequiredSecretState(label=label, present=False))
            continue
        states.append(
            RequiredSecretState(
                label=label,
                present=_env_secret_present(env_name),
            )
        )
    # Deduplicate by label while preserving order.
    seen: set[str] = set()
    unique: list[RequiredSecretState] = []
    for item in states:
        if item.label in seen:
            continue
        seen.add(item.label)
        unique.append(item)
    return tuple(unique)


def _legal_gate_allowed(settings: Settings, gate: str | None) -> bool:
    del settings
    if gate is None:
        return True
    # Legal gates are opt-out via bypass_config at runtime. Inventory reports the
    # gate label; default deployment policy treats missing gate as allowed.
    return True


def _engine_availability(
    name: str,
    capability: BypassCapability,
    *,
    settings: Settings,
    registered: Mapping[str, BypassCapability],
) -> tuple[CapabilityAvailability, str | None]:
    if name not in registered:
        return "unavailable", "engine not registered in this deployment"
    if capability.legal_gate and not _legal_gate_allowed(settings, capability.legal_gate):
        return "disabled", f"legal gate {capability.legal_gate} is not allowed"
    if name in {"residential_proxy", "proxy"} and not _proxy_configured(settings):
        return "unavailable", "proxy provider or list is not configured"
    if name == "captcha_solver":
        secrets = _captcha_secret_states(settings)
        paid_present = any(item.present and item.label not in {"browser_wait"} for item in secrets)
        wait_present = any(item.label == "browser_wait" and item.present for item in secrets)
        if paid_present:
            return "available", None
        if wait_present:
            return "degraded", "only passive browser_wait challenge path is configured"
        return "unavailable", "no captcha provider secret or browser_wait path configured"
    if capability.browser_family is not None and not settings.browser_headless:
        # Headed browser is available but needs explicit operator approval.
        return "available", "headed browser mode is enabled and requires approval"
    return "available", None


def _build_engine_entry(
    name: str,
    capability: BypassCapability,
    *,
    settings: Settings,
    registered: Mapping[str, BypassCapability],
) -> BrowserCapabilityEntry:
    group = _group_for_engine(name, capability)
    availability, reason = _engine_availability(
        name, capability, settings=settings, registered=registered
    )
    supports_js = capability.browser_family is not None
    supports_session = bool(
        capability.owns_session
        or name in {"session_handoff"}
        or (supports_js and settings.browser_session_state_enabled)
        or (supports_js and settings.browser_profile_persistent)
    )
    hard_timeout = (
        settings.browser_default_timeout_ms / 1000.0
        if supports_js
        else settings.career_site_timeout_seconds
    )
    max_concurrency = (
        settings.career_site_browser_concurrency
        if supports_js
        else settings.career_site_detail_concurrency
    )
    required_providers: list[str] = []
    required_secrets: tuple[RequiredSecretState, ...] = ()
    if group == "proxy_backed":
        required_providers.append(str(settings.proxy_provider or "raw"))
        required_secrets = _proxy_secret_states(settings)
    if group == "manual_challenge" or name == "captcha_solver":
        required_providers.append(str(settings.captcha_provider or "none"))
        required_secrets = _captcha_secret_states(settings)
    if capability.legal_gate:
        required_providers.append(f"legal_gate:{capability.legal_gate}")

    requires_approval = bool(
        supports_js
        or group in {"proxy_backed", "persistent_session", "manual_challenge"}
        or not settings.browser_headless
    )
    description = _ENGINE_DESCRIPTIONS.get(
        name,
        f"Registered bypass engine {name} ({capability.transport}).",
    )
    if availability != "available" and reason is None:
        reason = f"capability is {availability}"

    return BrowserCapabilityEntry(
        id=f"engine:{name}",
        group=group if availability != "unavailable" else group,
        availability=availability,
        reason=_safe_reason(reason) if reason else None,
        cost=int(capability.cost),
        risk=_risk_for_cost(int(capability.cost)),
        required_providers=tuple(required_providers),
        required_secrets=required_secrets,
        supports_js=supports_js,
        supports_session=supports_session,
        supports_proxy=bool(capability.supports_proxy),
        hard_timeout_seconds=float(hard_timeout),
        max_concurrency=int(max_concurrency),
        description=description,
        requires_approval=requires_approval,
        engine=name,
        legal_gate=capability.legal_gate,
    )


def _aggregate_group(
    group: CapabilityGroup,
    engines: Sequence[BrowserCapabilityEntry],
    *,
    settings: Settings,
) -> BrowserCapabilityEntry:
    members = [item for item in engines if item.group == group]
    if group == "persistent_session":
        session_enabled = bool(
            settings.browser_session_state_enabled or settings.browser_profile_persistent
        )
        browser_available = any(
            item.availability == "available" and item.supports_js for item in engines
        )
        if session_enabled and browser_available:
            availability: CapabilityAvailability = "available"
            reason = None
        elif not session_enabled:
            availability = "disabled"
            reason = "persistent browser session settings are disabled"
        else:
            availability = "unavailable"
            reason = "no browser engine is available for session reuse"
        cost = 25
        return BrowserCapabilityEntry(
            id=f"group:{group}",
            group=group,
            availability=availability,
            reason=_safe_reason(reason) if reason else None,
            cost=cost,
            risk=_risk_for_cost(cost),
            required_providers=("browser_session_state",),
            required_secrets=(),
            supports_js=True,
            supports_session=True,
            supports_proxy=True,
            hard_timeout_seconds=settings.browser_context_timeout_ms / 1000.0,
            max_concurrency=settings.career_site_browser_concurrency,
            description=_GROUP_DESCRIPTIONS[group],
            requires_approval=True,
        )

    if group == "proxy_backed":
        proxy_states = _proxy_secret_states(settings)
        configured = any(item.present for item in proxy_states)
        if configured:
            availability = "available"
            reason = None
        else:
            availability = "unavailable"
            reason = "no proxy list or gateway credentials are configured"
        # Prefer registered proxy engines when present.
        if members:
            best = min(members, key=lambda item: item.cost)
            cost = best.cost
            max_concurrency = best.max_concurrency
            hard_timeout = best.hard_timeout_seconds
        else:
            cost = 8
            max_concurrency = settings.career_site_detail_concurrency
            hard_timeout = settings.career_site_timeout_seconds
        return BrowserCapabilityEntry(
            id=f"group:{group}",
            group=group,
            availability=availability,
            reason=_safe_reason(reason) if reason else None,
            cost=cost,
            risk=_risk_for_cost(cost),
            required_providers=(str(settings.proxy_provider or "raw"),),
            required_secrets=proxy_states,
            supports_js=False,
            supports_session=False,
            supports_proxy=True,
            hard_timeout_seconds=hard_timeout,
            max_concurrency=max_concurrency,
            description=_GROUP_DESCRIPTIONS[group],
            requires_approval=True,
        )

    if group == "manual_challenge":
        secrets = _captcha_secret_states(settings)
        paid = any(item.present and item.label != "browser_wait" for item in secrets)
        wait_only = any(item.label == "browser_wait" and item.present for item in secrets)
        if paid:
            availability = "available"
            reason = None
        elif wait_only:
            availability = "degraded"
            reason = "provider secrets missing; passive browser_wait only"
        else:
            availability = "unavailable"
            reason = "challenge providers are not configured"
        cost = 50 if paid else 15
        return BrowserCapabilityEntry(
            id=f"group:{group}",
            group=group,
            availability=availability,
            reason=_safe_reason(reason) if reason else None,
            cost=cost,
            risk=_risk_for_cost(cost),
            required_providers=(str(settings.captcha_provider or "none"),),
            required_secrets=secrets,
            supports_js=True,
            supports_session=True,
            supports_proxy=True,
            hard_timeout_seconds=float(settings.captcha_solver_timeout_budget_seconds),
            max_concurrency=1,
            description=_GROUP_DESCRIPTIONS[group],
            requires_approval=True,
        )

    if group == "disabled_unavailable":
        unavailable = [
            item for item in engines if item.availability in {"unavailable", "disabled", "degraded"}
        ]
        if not unavailable:
            return BrowserCapabilityEntry(
                id=f"group:{group}",
                group=group,
                availability="available",
                reason="no disabled routes at the moment",
                cost=0,
                risk="low",
                description=_GROUP_DESCRIPTIONS[group],
            )
        reasons = sorted(
            {
                f"{item.engine or item.id}:{item.availability}"
                for item in unavailable
                if item.engine or item.id
            }
        )
        return BrowserCapabilityEntry(
            id=f"group:{group}",
            group=group,
            availability="unavailable",
            reason=_safe_reason("; ".join(reasons[:8])),
            cost=0,
            risk="low",
            description=_GROUP_DESCRIPTIONS[group],
            requires_approval=False,
        )

    # direct_http / stealth_http_tls / browser aggregates from members
    available_members = [item for item in members if item.availability == "available"]
    degraded_members = [item for item in members if item.availability == "degraded"]
    if available_members:
        best = min(available_members, key=lambda item: item.cost)
        availability = "available"
        reason = None
        cost = best.cost
        representative = best
    elif degraded_members:
        best = min(degraded_members, key=lambda item: item.cost)
        availability = "degraded"
        reason = best.reason or "degraded members only"
        cost = best.cost
        representative = best
    elif members:
        best = min(members, key=lambda item: item.cost)
        availability = best.availability
        reason = best.reason or f"no usable {group} engine"
        cost = best.cost
        representative = best
    else:
        availability = "unavailable"
        reason = f"no {group} engines registered"
        cost = 0
        representative = None

    return BrowserCapabilityEntry(
        id=f"group:{group}",
        group=group,
        availability=availability,
        reason=_safe_reason(reason) if reason else None,
        cost=cost,
        risk=_risk_for_cost(cost),
        required_providers=(),
        required_secrets=(),
        supports_js=bool(representative.supports_js) if representative else group == "browser",
        supports_session=bool(representative.supports_session) if representative else False,
        supports_proxy=bool(representative.supports_proxy) if representative else True,
        hard_timeout_seconds=(
            representative.hard_timeout_seconds
            if representative is not None
            else (
                settings.browser_default_timeout_ms / 1000.0
                if group == "browser"
                else settings.career_site_timeout_seconds
            )
        ),
        max_concurrency=(
            representative.max_concurrency
            if representative is not None
            else (
                settings.career_site_browser_concurrency
                if group == "browser"
                else settings.career_site_detail_concurrency
            )
        ),
        description=_GROUP_DESCRIPTIONS[group],
        requires_approval=group
        in {"browser", "persistent_session", "proxy_backed", "manual_challenge"},
    )


def build_browser_capability_inventory(
    settings: Settings | None = None,
    *,
    registered: Mapping[str, BypassCapability] | None = None,
) -> BrowserCapabilityInventory:
    """Build a read-only inventory of browser/bypass capabilities."""
    resolved_settings = settings or get_settings()
    try:
        caps = dict(registered) if registered is not None else list_bypass_capabilities()
    except Exception as exc:  # noqa: BLE001 - inventory boundary
        return BrowserCapabilityInventory(
            generated_at=datetime.now(UTC),
            status="error",
            capability_count=0,
            error=_safe_reason(str(exc), fallback="capability registry unavailable"),
            notes=("inventory is read-only and does not start browsers",),
        )

    engine_entries = [
        _build_engine_entry(name, capability, settings=resolved_settings, registered=caps)
        for name, capability in sorted(caps.items(), key=lambda item: (item[1].cost, item[0]))
    ]
    # Ensure default fallback engines appear even when optional extras are missing.
    present = {item.engine for item in engine_entries if item.engine}
    for name in DEFAULT_FALLBACK_ORDER:
        if name in present:
            continue
        engine_entries.append(
            BrowserCapabilityEntry(
                id=f"engine:{name}",
                group=_ENGINE_GROUP.get(name, "disabled_unavailable"),
                availability="unavailable",
                reason="engine not registered in this deployment",
                cost=DEFAULT_FALLBACK_ORDER.index(name) * 10,
                risk=_risk_for_cost(DEFAULT_FALLBACK_ORDER.index(name) * 10),
                description=_ENGINE_DESCRIPTIONS.get(name, f"Unregistered engine {name}."),
                supports_js=name
                in {
                    "stealth_browser",
                    "patchright_browser",
                    "nodriver",
                    "camoufox",
                    "cloak",
                },
                supports_session=False,
                supports_proxy=True,
                hard_timeout_seconds=resolved_settings.career_site_timeout_seconds,
                max_concurrency=resolved_settings.career_site_browser_concurrency,
                requires_approval=True,
                engine=name,
            )
        )

    groups: list[CapabilityGroup] = [
        "direct_http",
        "stealth_http_tls",
        "browser",
        "persistent_session",
        "proxy_backed",
        "manual_challenge",
        "disabled_unavailable",
    ]
    group_entries = [
        _aggregate_group(group, engine_entries, settings=resolved_settings) for group in groups
    ]

    # Inventory lists group summaries first, then concrete engines.
    capabilities = tuple(group_entries + engine_entries)
    fallback = tuple(name for name in DEFAULT_FALLBACK_ORDER if name in present) or ("noop",)
    notes = (
        "inventory is read-only and does not start browsers",
        "secret values and proxy endpoints are never included",
        "sensitive routes require explicit operator approval before execution",
    )
    return BrowserCapabilityInventory(
        generated_at=datetime.now(UTC),
        status="ok",
        capability_count=len(capabilities),
        fallback_order=fallback,
        capabilities=capabilities,
        notes=notes,
    )


def _source_kind(source: Mapping[str, Any] | None) -> str | None:
    if not source:
        return None
    for key in ("kind", "type", "source_type"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    spec = source.get("spec")
    if isinstance(spec, Mapping):
        value = spec.get("type")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _requested_bypass(source: Mapping[str, Any] | None, override: str | None) -> str | None:
    if override is not None and override.strip():
        return override.strip()
    if not source:
        return None
    for key in ("bypass", "requested_bypass"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    spec = source.get("spec")
    if isinstance(spec, Mapping):
        value = spec.get("bypass")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _assessment_hints(source: Mapping[str, Any] | None) -> list[str]:
    if not source:
        return []
    assessment = source.get("assessment")
    if not isinstance(assessment, Mapping):
        return []
    hints: list[str] = []
    freshness = assessment.get("freshness")
    if isinstance(freshness, Mapping):
        if freshness.get("probe_blocked"):
            hints.append("assessment reports probe_blocked; browser or proxy may be required")
        if freshness.get("probe_failed"):
            hints.append("assessment reports probe_failed; escalate only after HTTP retry budget")
        if freshness.get("dates_require_detail_scrape"):
            hints.append("detail scrape may require browser if listing is SPA-rendered")
    capabilities = assessment.get("capabilities")
    if isinstance(capabilities, Mapping):
        if capabilities.get("has_embedded_state"):
            hints.append("source has embedded state; JS-capable route preferred after HTTP fails")
        if capabilities.get("known_integration"):
            hints.append("known integration path exists; prefer cheap direct HTTP first")
    return hints


def explain_route_plan(
    *,
    settings: Settings | None = None,
    source: Mapping[str, Any] | None = None,
    source_id: str | None = None,
    requested_bypass: str | None = None,
    registered: Mapping[str, BypassCapability] | None = None,
) -> RoutePlanExplanation:
    """Explain which route would be selected/unavailable without executing it."""
    inventory = build_browser_capability_inventory(settings, registered=registered)
    if inventory.status == "error":
        return RoutePlanExplanation(
            generated_at=datetime.now(UTC),
            source_id=source_id,
            source_kind=_source_kind(source),
            requested_bypass=_requested_bypass(source, requested_bypass),
            error=inventory.error or "capability inventory unavailable",
        )

    engines = {
        item.engine: item
        for item in inventory.capabilities
        if item.engine is not None and item.id.startswith("engine:")
    }
    groups = {item.group: item for item in inventory.capabilities if item.id.startswith("group:")}
    bypass = _requested_bypass(source, requested_bypass)
    kind = _source_kind(source)
    notes = list(_assessment_hints(source))
    notes.append("diagnostics are advisory; no browser or proxy is started")

    selected_id: str | None = None
    selected_group: CapabilityGroup | None = None
    diagnostics: list[RouteCapabilityDiagnostic] = []

    def _status_for(entry: BrowserCapabilityEntry) -> RouteDiagnosticStatus:
        if entry.availability == "available":
            return "available"
        if entry.availability == "disabled":
            return "blocked"
        if entry.availability == "degraded":
            return "available"
        return "unavailable"

    # Explicit bypass pin.
    if bypass and bypass not in {"auto", "adaptive"}:
        entry = engines.get(bypass)
        if entry is None:
            diagnostics.append(
                RouteCapabilityDiagnostic(
                    capability_id=f"engine:{bypass}",
                    group="disabled_unavailable",
                    status="unavailable",
                    reason=_safe_reason(f"requested bypass {bypass} is not registered"),
                    cost=0,
                    risk="low",
                    engine=bypass,
                )
            )
            notes.append(f"requested bypass {bypass} is unavailable; falling back to auto order")
        elif entry.availability not in {"available", "degraded"}:
            diagnostics.append(
                RouteCapabilityDiagnostic(
                    capability_id=entry.id,
                    group=entry.group,
                    status="unavailable",
                    reason=_safe_reason(entry.reason or "requested bypass is not available"),
                    cost=entry.cost,
                    risk=entry.risk,
                    engine=entry.engine,
                )
            )
            notes.append(f"requested bypass {bypass} is not currently usable")
        else:
            selected_id = entry.id
            selected_group = entry.group
            diagnostics.append(
                RouteCapabilityDiagnostic(
                    capability_id=entry.id,
                    group=entry.group,
                    status="selected",
                    reason="explicit source bypass pin",
                    cost=entry.cost,
                    risk=entry.risk,
                    engine=entry.engine,
                )
            )

    # Auto selection from preferred groups + fallback order.
    if selected_id is None:
        preferred_groups: list[CapabilityGroup]
        if kind in {"telegram_channel", "telegram_group", "telegram_comments", "rss_feed"}:
            preferred_groups = ["direct_http"]
            notes.append("non-browser source kinds prefer direct HTTP")
        elif kind in {"browser"}:
            preferred_groups = ["browser", "stealth_http_tls", "direct_http"]
            notes.append("browser source kind prefers JS-capable routes")
        else:
            preferred_groups = ["direct_http", "stealth_http_tls", "browser"]

        assessment = source.get("assessment") if isinstance(source, Mapping) else None
        freshness = assessment.get("freshness") if isinstance(assessment, Mapping) else None
        if isinstance(freshness, Mapping) and freshness.get("probe_blocked"):
            preferred_groups = ["stealth_http_tls", "browser", "proxy_backed", "direct_http"]
            notes.append("probe_blocked shifts preference toward stealth/browser/proxy")

        fallback_index = {name: index for index, name in enumerate(inventory.fallback_order)}

        def _group_rank(group: CapabilityGroup) -> int:
            try:
                return preferred_groups.index(group)
            except ValueError:
                return len(preferred_groups) + 1

        ranked: list[BrowserCapabilityEntry] = []
        for engine_name in inventory.fallback_order:
            entry = engines.get(engine_name)
            if entry is None:
                continue
            ranked.append(entry)
        ranked.sort(
            key=lambda item: (
                _group_rank(item.group),
                item.cost,
                fallback_index.get(item.engine or "", 10_000),
                item.id,
            )
        )

        for entry in ranked:
            entry_status = _status_for(entry)
            entry_available = entry_status == "available"
            if selected_id is None and entry_available:
                selected_id = entry.id
                selected_group = entry.group
                diagnostics.append(
                    RouteCapabilityDiagnostic(
                        capability_id=entry.id,
                        group=entry.group,
                        status="selected",
                        reason="first available engine in preferred fallback order",
                        cost=entry.cost,
                        risk=entry.risk,
                        engine=entry.engine,
                    )
                )
            else:
                reason = entry.reason or (
                    "available but lower priority than selected route"
                    if entry_available
                    else entry.availability
                )
                diagnostic_status: RouteDiagnosticStatus = (
                    "skipped" if entry_available else entry_status
                )
                diagnostics.append(
                    RouteCapabilityDiagnostic(
                        capability_id=entry.id,
                        group=entry.group,
                        status=diagnostic_status,
                        reason=_safe_reason(reason),
                        cost=entry.cost,
                        risk=entry.risk,
                        engine=entry.engine,
                    )
                )

    # Always include group-level summaries for planner visibility.
    for group_name, entry in groups.items():
        if any(item.capability_id == entry.id for item in diagnostics):
            continue
        if selected_group == group_name and selected_id and selected_id.startswith("group:"):
            group_status: RouteDiagnosticStatus = "selected"
            reason = "selected capability group"
        else:
            group_status = _status_for(entry)
            if group_status == "available" and selected_id is not None:
                group_status = "skipped"
            reason = entry.reason or entry.availability
        diagnostics.append(
            RouteCapabilityDiagnostic(
                capability_id=entry.id,
                group=entry.group,
                status=group_status,
                reason=_safe_reason(reason),
                cost=entry.cost,
                risk=entry.risk,
                engine=entry.engine,
            )
        )

    # Sort: selected first, then by cost.
    diagnostics.sort(
        key=lambda item: (
            0 if item.status == "selected" else 1,
            item.cost,
            item.capability_id,
        )
    )

    return RoutePlanExplanation(
        generated_at=datetime.now(UTC),
        source_id=source_id or (str(source.get("source_id")) if source else None),
        source_kind=kind,
        requested_bypass=bypass,
        selected_capability_id=selected_id,
        selected_group=selected_group,
        fallback_order=inventory.fallback_order,
        diagnostics=tuple(diagnostics),
        notes=tuple(notes),
    )


def inventory_to_public_dict(inventory: BrowserCapabilityInventory) -> dict[str, Any]:
    """Serialize inventory with an explicit denylist of sensitive keys."""
    payload = inventory.model_dump(mode="json")
    return cast("dict[str, Any]", _redact_payload(payload))


def explanation_to_public_dict(explanation: RoutePlanExplanation) -> dict[str, Any]:
    """Serialize route explanation with sensitive-key scrubbing."""
    payload = explanation.model_dump(mode="json")
    return cast("dict[str, Any]", _redact_payload(payload))


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(
                marker in lowered
                for marker in (
                    "cookie",
                    "token",
                    "password",
                    "secret",
                    "proxy_url",
                    "authorization",
                    "profile_path",
                    "executable",
                    "api_key",
                )
            ):
                continue
            redacted[str(key)] = _redact_payload(nested)
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in _SENSITIVE_SNIPPET_MARKERS):
            return "redacted"
        return value
    return value
