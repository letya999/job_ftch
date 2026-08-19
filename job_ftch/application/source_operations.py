"""Source-scoped probe/run helpers for operator adapters.

MCP calls these instead of importing browser clients. Dedicated live browser
sessions are not implemented. Ingest reuses TenantRunner source_ids runs.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from job_ftch.application.browser_capability_inventory import explanation_to_public_dict
from job_ftch.domain.runtime_source import source_spec_identifier
from job_ftch.domain.source_spec import SourceSpec

PROBE_MODES = frozenset({"cheap", "full"})
ESCALATION_STRATEGIES = frozenset({"recommended", "all"})
BROWSER_PROBES = frozenset({"listing", "detail", "challenge", "fingerprint", "custom_safe"})
BROWSER_ENGINES = frozenset({"auto", "patchright", "nodriver", "camoufox", "cloak"})
_BROWSER_GROUPS = frozenset({"browser", "persistent_session", "manual_challenge"})
_BROWSER_ENGINES = frozenset(
    {
        "stealth_browser",
        "patchright_browser",
        "patchright",
        "nodriver",
        "camoufox",
        "cloak",
    }
)
_MISSING_BROWSER_PROBE = "browser_session_probe"
_MISSING_BYPASS_OVERRIDE = "forced_bypass_override"
_MISSING_ESCALATION_SWEEP = "independent_bypass_sweep"


def _notes(*items: str) -> list[str]:
    return [item for item in items if item]


def _envelope(
    *,
    tenant_id: str | None,
    source_id: str | None,
    status: str,
    ok: bool = False,
    executed: bool = False,
    error: str | None = None,
    missing_service: str | None = None,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    **legacy_extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "executed": executed,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "error": error,
        "missing_service": missing_service,
        "notes": notes or [],
    }
    if extra:
        payload.update(extra)
    if legacy_extra:
        payload.update(legacy_extra)
    return payload


def _source_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    spec_raw = source.get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    requirements_raw = source.get("requirements")
    requirements: dict[str, Any] = requirements_raw if isinstance(requirements_raw, dict) else {}
    return {
        "source_id": source.get("source_id"),
        "source_kind": source.get("source_kind") or source.get("type"),
        "source_name": source.get("source_name"),
        "origin": source.get("origin"),
        "enabled": source.get("enabled"),
        "status": source.get("status"),
        "degraded": bool(source.get("degraded")),
        "requirements": {
            "browser_required": bool(requirements.get("browser_required")),
            "browser_reason": requirements.get("browser_reason"),
            "browser_setup_hint": requirements.get("browser_setup_hint"),
        },
        "current_bypass": spec.get("bypass"),
        "current_parser": spec.get("parser") or spec.get("parser_kind"),
    }


def _canonical_source_id(source: dict[str, Any]) -> str | None:
    spec = source.get("spec")
    if not isinstance(spec, dict):
        return None
    try:
        model: SourceSpec = TypeAdapter(SourceSpec).validate_python(spec)
    except (TypeError, ValueError):
        return None
    return source_spec_identifier(model)


def _source_aliases(source: dict[str, Any]) -> set[str]:
    aliases = {str(source.get("source_id") or "")}
    canonical = _canonical_source_id(source)
    if canonical:
        aliases.add(canonical)
    spec_raw = source.get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    kind = str(source.get("source_kind") or spec.get("type") or "")
    name = str(source.get("source_name") or spec.get("source_name") or "")
    if kind and name:
        aliases.add(f"{kind}:{name}")
    aliases.discard("")
    return aliases


async def _find_source(runner: Any, tenant_id: str, source_id: str) -> dict[str, Any] | None:
    sources = await runner.list_sources(tenant_id)
    if not isinstance(sources, list):
        return None
    for item in sources:
        if isinstance(item, dict) and source_id in _source_aliases(item):
            return item
    return None


def _run_source_id(source: dict[str, Any], requested: str) -> str:
    return _canonical_source_id(source) or requested


async def _route_payload(
    runner: Any,
    tenant_id: str,
    source_id: str,
    *,
    bypass: str | None = None,
) -> dict[str, Any]:
    explanation = await runner.explain_browser_route(tenant_id, source_id, bypass=bypass)
    if hasattr(explanation, "model_dump"):
        return explanation_to_public_dict(explanation)
    if isinstance(explanation, dict):
        return explanation
    return {"error": "route_unavailable", "source_id": source_id}


def _selected_route(route: dict[str, Any]) -> dict[str, Any]:
    selected_engine = None
    selected_group = route.get("selected_group")
    for item in route.get("diagnostics") or []:
        if isinstance(item, dict) and item.get("status") == "selected":
            selected_engine = item.get("engine")
            selected_group = item.get("group") or selected_group
            break
    return {
        "capability_id": route.get("selected_capability_id"),
        "group": selected_group,
        "engine": selected_engine,
    }


def _is_browser_route(route: dict[str, Any], source: dict[str, Any] | None = None) -> bool:
    selected = _selected_route(route)
    if selected["group"] in _BROWSER_GROUPS:
        return True
    if selected["engine"] in _BROWSER_ENGINES:
        return True
    requirements = (source or {}).get("requirements") or {}
    return bool(requirements.get("browser_required"))


def _summary_payload(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {}
    if hasattr(summary, "as_dict"):
        raw = summary.as_dict()
    elif isinstance(summary, dict):
        raw = summary
    else:
        return {"repr": type(summary).__name__}
    return {
        "tenant_id": raw.get("tenant_id"),
        "source_run_id": raw.get("source_run_id"),
        "fetched": raw.get("fetched"),
        "emitted": raw.get("emitted"),
        "failed": raw.get("failed"),
        "skipped_already_active": raw.get("skipped_already_active"),
        "source_failures": raw.get("source_failures") or [],
        "source_outcomes": raw.get("source_outcomes") or [],
        "drop_reasons": raw.get("drop_reasons") or {},
    }


def _run_status(summary: dict[str, Any]) -> tuple[str, bool]:
    if summary.get("skipped_already_active"):
        return "degraded", False
    failed = int(summary.get("failed") or 0)
    fetched = int(summary.get("fetched") or 0)
    if failed:
        return "degraded", False
    if fetched == 0:
        return "empty", True
    return "ok", True


async def _execute_source_run(
    runner: Any,
    *,
    tenant_id: str,
    source: dict[str, Any],
    requested_source_id: str,
    max_items: int | None,
) -> dict[str, Any]:
    try:
        summary = await runner.run_tenant(
            tenant_id,
            max_items=max_items,
            source_ids=[_run_source_id(source, requested_source_id)],
        )
    except ValueError as exc:
        return {
            "ok": False,
            "status": "error",
            "executed": False,
            "error": str(exc)[:200],
            "run": None,
        }
    payload = _summary_payload(summary)
    status, ok = _run_status(payload)
    return {
        "ok": ok,
        "status": status,
        "executed": True,
        "error": None,
        "run": payload,
    }


async def probe_source(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    mode: str = "cheap",
    max_items: int = 5,
) -> dict[str, Any]:
    normalized = (mode or "cheap").strip().lower()
    if normalized not in PROBE_MODES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_mode",
            notes=_notes(f"mode must be one of {sorted(PROBE_MODES)}"),
            mode=mode,
        )
    if max_items is None or int(max_items) <= 0:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="invalid_max_items",
            notes=_notes("max_items must be a positive integer"),
            mode=normalized,
        )
    source = await _find_source(runner, tenant_id, source_id)
    if source is None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_not_found",
            error="source_not_found",
            mode=normalized,
        )
    if source.get("enabled") is False:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_disabled",
            error="source_disabled",
            mode=normalized,
            source=_source_snapshot(source),
        )
    route = await _route_payload(runner, tenant_id, source_id)
    snapshot = _source_snapshot(source)
    selected = _selected_route(route)
    common: dict[str, Any] = {
        "mode": normalized,
        "source": snapshot,
        "route": route,
        "selected_route": selected,
        "browser_required": _is_browser_route(route, source),
        "max_items": int(max_items),
    }
    if normalized == "cheap":
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="ok",
            ok=True,
            notes=_notes(
                "cheap probe is diagnostics-only; no fetch or browser session is started",
                "use mode=full or run_source to ingest through TenantRunner",
            ),
            extra=common,
        )
    executed = await _execute_source_run(
        runner,
        tenant_id=tenant_id,
        source=source,
        requested_source_id=source_id,
        max_items=int(max_items),
    )
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed["status"]),
        ok=bool(executed["ok"]),
        executed=bool(executed["executed"]),
        error=executed.get("error"),
        notes=_notes("full probe runs the existing source-scoped tenant pipeline"),
        extra={**common, "run": executed.get("run")},
    )


async def run_source(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    max_items: int | None = None,
    parser: str | None = None,
    bypass: str | None = None,
) -> dict[str, Any]:
    if max_items is not None and int(max_items) <= 0:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="invalid_max_items",
            notes=_notes("max_items must be a positive integer when provided"),
        )
    source = await _find_source(runner, tenant_id, source_id)
    if source is None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_not_found",
            error="source_not_found",
        )
    if source.get("enabled") is False:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_disabled",
            error="source_disabled",
            source=_source_snapshot(source),
        )
    route = await _route_payload(runner, tenant_id, source_id)
    snapshot = _source_snapshot(source)
    selected = _selected_route(route)
    requested_bypass = (bypass or "").strip() or None
    requested_parser = (parser or "").strip() or None
    if requested_bypass and requested_bypass not in {"auto", "adaptive"}:
        allowed = {snapshot.get("current_bypass"), selected.get("engine")}
        if requested_bypass not in allowed:
            route = await _route_payload(runner, tenant_id, source_id, bypass=requested_bypass)
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="unsupported",
                error="bypass_override_unsupported",
                missing_service=_MISSING_BYPASS_OVERRIDE,
                notes=_notes(
                    "run_source cannot pin a different bypass for one call",
                    "adaptive escalation stays inside the source fetch",
                    f"current/selected bypass is {selected.get('engine') or snapshot.get('current_bypass')}",
                ),
                source=snapshot,
                route=route,
                selected_route=selected,
                requested_bypass=requested_bypass,
                requested_parser=requested_parser,
            )
    if requested_parser and requested_parser not in {"auto"}:
        current_parser = snapshot.get("current_parser")
        if current_parser and requested_parser != current_parser:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="unsupported",
                error="parser_override_unsupported",
                notes=_notes(
                    "run_source cannot pin a different parser for one call",
                    f"current parser is {current_parser}",
                ),
                source=snapshot,
                route=route,
                selected_route=selected,
                requested_bypass=requested_bypass,
                requested_parser=requested_parser,
            )
    executed = await _execute_source_run(
        runner,
        tenant_id=tenant_id,
        source=source,
        requested_source_id=source_id,
        max_items=max_items,
    )
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed["status"]),
        ok=bool(executed["ok"]),
        executed=bool(executed["executed"]),
        error=executed.get("error"),
        notes=_notes("run_source is a source-scoped TenantRunner ingest"),
        source=snapshot,
        route=route,
        selected_route=selected,
        requested_bypass=requested_bypass,
        requested_parser=requested_parser,
        run=executed.get("run"),
        browser_required=_is_browser_route(route, source),
        max_items=max_items,
    )


async def run_source_escalation(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    strategy: str = "recommended",
    max_tier: str | None = None,
    max_items: int = 5,
) -> dict[str, Any]:
    normalized = (strategy or "recommended").strip().lower()
    if normalized not in ESCALATION_STRATEGIES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_strategy",
            notes=_notes(f"strategy must be one of {sorted(ESCALATION_STRATEGIES)}"),
            strategy=strategy,
        )
    if max_items is None or int(max_items) <= 0:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="invalid_max_items",
            strategy=normalized,
        )
    if max_tier is not None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="max_tier_not_implemented",
            missing_service=_MISSING_ESCALATION_SWEEP,
            notes=_notes(
                "max_tier is not exposed as an application port",
                "career-site fetch already escalates adaptively inside the source",
            ),
            strategy=normalized,
            max_tier=max_tier,
        )
    if normalized == "all":
        source = await _find_source(runner, tenant_id, source_id)
        route = (
            await _route_payload(runner, tenant_id, source_id)
            if source is not None
            else {"error": "source_not_found"}
        )
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="independent_bypass_sweep_not_implemented",
            missing_service=_MISSING_ESCALATION_SWEEP,
            notes=_notes(
                "strategy=all would need an independent per-bypass probe service",
                "use strategy=recommended to run adaptive ingest once",
            ),
            strategy=normalized,
            source=None if source is None else _source_snapshot(source),
            route=route,
            escalation_ladder=list(route.get("fallback_order") or []),
        )
    result = await run_source(
        runner,
        tenant_id=tenant_id,
        source_id=source_id,
        max_items=int(max_items),
    )
    result["strategy"] = normalized
    result["escalation_ladder"] = list((result.get("route") or {}).get("fallback_order") or [])
    result["notes"] = _notes(
        *list(result.get("notes") or []),
        "recommended escalation is the source's own adaptive bypass ladder",
    )
    return result


async def probe_bypass_route(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    bypass: str,
    max_items: int = 3,
) -> dict[str, Any]:
    requested = (bypass or "").strip()
    if not requested:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="bypass_required",
            notes=_notes("bypass is required"),
        )
    if max_items is None or int(max_items) <= 0:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="invalid_max_items",
            requested_bypass=requested,
        )
    source = await _find_source(runner, tenant_id, source_id)
    if source is None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_not_found",
            error="source_not_found",
            requested_bypass=requested,
        )
    current_route = await _route_payload(runner, tenant_id, source_id)
    current_selected = _selected_route(current_route)
    route = await _route_payload(runner, tenant_id, source_id, bypass=requested)
    selected = _selected_route(route)
    snapshot = _source_snapshot(source)
    engine_diag = next(
        (
            item
            for item in (route.get("diagnostics") or [])
            if isinstance(item, dict)
            and (
                item.get("engine") == requested
                or item.get("capability_id") == f"engine:{requested}"
            )
        ),
        None,
    )
    requested_group = engine_diag.get("group") if isinstance(engine_diag, dict) else None
    diagnosed: dict[str, Any] = {
        "requested_bypass": requested,
        "source": snapshot,
        "route": route,
        "selected_route": selected,
        "requested_diagnostic": engine_diag,
        "max_items": int(max_items),
        "browser_required": requested in _BROWSER_ENGINES or requested_group in _BROWSER_GROUPS,
    }
    if source.get("enabled") is False:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="source_disabled",
            error="source_disabled",
            extra=diagnosed,
        )
    if engine_diag is not None and engine_diag.get("status") in {"unavailable", "blocked"}:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unavailable",
            error="bypass_unavailable",
            notes=_notes("requested bypass is not usable in this runtime"),
            extra=diagnosed,
        )
    if diagnosed["browser_required"]:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="live_bypass_probe_not_implemented",
            missing_service=_MISSING_BROWSER_PROBE,
            notes=_notes(
                "no dedicated live browser/bypass probe service exists",
                "use get_bypass_routes for diagnostics or run_source for adaptive ingest",
            ),
            extra=diagnosed,
        )
    matches_selected = requested in {
        current_selected.get("engine"),
        snapshot.get("current_bypass"),
        "auto",
        "adaptive",
    }
    if not matches_selected:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="forced_bypass_override_not_implemented",
            missing_service=_MISSING_BYPASS_OVERRIDE,
            notes=_notes(
                "cannot force a non-selected bypass for one probe",
                "diagnostics above describe the requested route without executing it",
            ),
            extra=diagnosed,
        )
    executed = await _execute_source_run(
        runner,
        tenant_id=tenant_id,
        source=source,
        requested_source_id=source_id,
        max_items=int(max_items),
    )
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed["status"]),
        ok=bool(executed["ok"]),
        executed=bool(executed["executed"]),
        error=executed.get("error"),
        notes=_notes("probe executed the current/selected non-browser route via TenantRunner"),
        extra={**diagnosed, "run": executed.get("run")},
    )


async def run_browser_probe(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str | None = None,
    url: str | None = None,
    probe: str = "listing",
    engine: str = "auto",
    bypass: str | None = None,
    headed: bool = False,
    max_items: int = 5,
) -> dict[str, Any]:
    del url, headed, max_items
    normalized_probe = (probe or "listing").strip().lower()
    normalized_engine = (engine or "auto").strip().lower()
    if normalized_probe not in BROWSER_PROBES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_probe",
            notes=_notes(f"probe must be one of {sorted(BROWSER_PROBES)}"),
            probe=probe,
            engine=normalized_engine,
        )
    if normalized_engine not in BROWSER_ENGINES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_engine",
            notes=_notes(f"engine must be one of {sorted(BROWSER_ENGINES)}"),
            probe=normalized_probe,
            engine=engine,
        )
    route: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    if source_id:
        source = await _find_source(runner, tenant_id, source_id)
        if source is None:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="source_not_found",
                error="source_not_found",
                probe=normalized_probe,
                engine=normalized_engine,
                requested_bypass=bypass,
            )
        route = await _route_payload(runner, tenant_id, source_id, bypass=bypass)
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status="not_implemented",
        error="live_browser_probe_not_implemented",
        missing_service=_MISSING_BROWSER_PROBE,
        notes=_notes(
            "no safe application browser-session service exists yet",
            "this tool does not start Patchright/nodriver/camoufox/cloak",
            "use get_bypass_routes / recommend_runtime_setup, then run_source for ingest",
        ),
        probe=normalized_probe,
        engine=normalized_engine,
        requested_bypass=bypass,
        source=None if source is None else _source_snapshot(source),
        route=route,
        selected_route=None if route is None else _selected_route(route),
    )
