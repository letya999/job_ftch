"""Source-scoped probe/run helpers for operator adapters.

MCP calls these instead of importing browser clients. Listing/detail/challenge
probes and ephemeral sessions go through TenantRunner. Ingest reuses
TenantRunner source_ids runs.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from job_ftch.application.browser_capability_inventory import explanation_to_public_dict
from job_ftch.application.registry import (
    all_monitor_names,
    all_scraper_names,
    all_site_parser_names,
    list_bypass_capabilities,
    site_parser_domain_pattern,
)
from job_ftch.application.tenant_runner import OperatorSessionAttachError
from job_ftch.domain.runtime_source import source_spec_identifier
from job_ftch.domain.source_spec import SourceSpec

PROBE_MODES = frozenset({"cheap", "full"})
ESCALATION_STRATEGIES = frozenset({"recommended", "all"})
BROWSER_PROBES = frozenset({"listing", "detail", "challenge", "fingerprint", "custom_safe"})
BROWSER_ENGINES = frozenset(
    {
        "auto",
        "playwright",
        "stealth_browser",
        "patchright",
        "patchright_browser",
        "nodriver",
        "camoufox",
        "cloak",
    }
)
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
_LIVE_PROBES = frozenset({"listing", "detail", "challenge", "fingerprint", "custom_safe"})
_SESSION_PROFILES = frozenset({"ephemeral", "persistent", "domain"})
_SWEEP_MAX_ROUTES = 8
_ADAPTIVE_BYPASS = frozenset({"auto", "adaptive"})
_ENGINE_ALIASES = {
    "auto": "patchright_browser",
    "playwright": "stealth_browser",
    "stealth_browser": "stealth_browser",
    "patchright": "patchright_browser",
    "patchright_browser": "patchright_browser",
    "nodriver": "nodriver",
    "camoufox": "camoufox",
    "cloak": "cloak",
}


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


def _listing_url(source: dict[str, Any] | None, explicit: str | None) -> str | None:
    if explicit and str(explicit).strip():
        candidate = str(explicit).strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
        return None
    spec_raw = (source or {}).get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    for key in ("url", "listing_url", "start_url", "base_url"):
        value = spec.get(key)
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            return text
    return None


def _resolve_listing_engine(engine: str) -> str:
    normalized = (engine or "auto").strip().lower()
    return _ENGINE_ALIASES.get(normalized, normalized)


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
        "extracted": raw.get("extracted"),
        "emitted": raw.get("emitted"),
        "failed": raw.get("failed"),
        "review": raw.get("review"),
        "rejected": raw.get("rejected"),
        "skipped_already_active": raw.get("skipped_already_active"),
        "source_failures": raw.get("source_failures") or [],
        "source_outcomes": raw.get("source_outcomes") or [],
        "drop_reasons": raw.get("drop_reasons") or {},
    }


def _normalize_bypass_name(name: str | None) -> str | None:
    text = (name or "").strip()
    if not text or text.lower() in _ADAPTIVE_BYPASS:
        return None
    return _ENGINE_ALIASES.get(text.lower(), text)


def _registered_bypass_names() -> set[str]:
    try:
        return set(list_bypass_capabilities())
    except Exception:
        return set()


def _parser_pin_error(source: dict[str, Any], requested: str) -> str | None:
    spec_raw = source.get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    kind = str(spec.get("type") or source.get("source_kind") or "")
    if kind == "career_site":
        try:
            monitors = all_monitor_names()
            scrapers = all_scraper_names()
            site_parsers = all_site_parser_names()
        except Exception:
            monitors, scrapers, site_parsers = frozenset(), frozenset(), frozenset()
        if requested in monitors or requested in scrapers or requested in site_parsers:
            return None
        return "parser_unavailable"
    if kind == "declarative_html":
        try:
            names = all_site_parser_names()
        except Exception:
            names = frozenset()
        if names and requested not in names and requested != "auto":
            return "parser_unavailable"
        return None
    if kind == "browser":
        return None
    return "parser_pin_unsupported_source"


def _parser_host_mismatch(source: dict[str, Any], requested: str) -> str | None:
    import re

    spec_raw = source.get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    url = str(spec.get("url") or "")
    pattern = site_parser_domain_pattern(requested)
    if not pattern or not url:
        return None
    try:
        if re.search(pattern, url, re.IGNORECASE):
            return None
    except re.error:
        return None
    return f"parser_host_mismatch: {requested} is URL-bound; pinned onto {url} for this call only"


def _parse_diagnosis(
    *,
    run: dict[str, Any] | None = None,
    listing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if listing is not None:
        items = listing.get("items") or []
        challenge = listing.get("challenge")
        if challenge and not items:
            return {
                "ok": False,
                "stage": "fetch",
                "reason": "challenge_detected",
                "detail": challenge,
            }
        if items:
            return {
                "ok": True,
                "stage": "listing",
                "reason": "listing_cards",
                "detail": None,
            }
        return {
            "ok": False,
            "stage": "listing",
            "reason": "no_listing_cards",
            "detail": None,
        }
    payload = run or {}
    outcomes = payload.get("source_outcomes") or []
    first = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else {}
    zero_reason = first.get("zero_reason")
    outcome_error = first.get("error")
    failure_error = None
    failures = payload.get("source_failures") or []
    if failures and isinstance(failures[0], dict):
        failure_error = failures[0].get("error")
    fetched = int(payload.get("fetched") or 0)
    extracted = int(payload.get("extracted") or 0)
    yielded = int(first.get("yielded") or 0)
    failed = int(payload.get("failed") or 0)
    if failure_error or outcome_error:
        return {
            "ok": False,
            "stage": "fetch",
            "reason": str(failure_error or outcome_error or "source_fetch_failed"),
            "detail": zero_reason,
        }
    if zero_reason:
        return {
            "ok": False,
            "stage": "parse",
            "reason": str(zero_reason),
            "detail": first.get("status"),
        }
    if fetched == 0:
        return {
            "ok": False,
            "stage": "fetch",
            "reason": "empty_fetch",
            "detail": first.get("status"),
        }
    if yielded == 0 and extracted == 0:
        return {
            "ok": False,
            "stage": "parse",
            "reason": "fetched_but_not_parsed",
            "detail": first.get("status"),
        }
    if failed:
        return {
            "ok": False,
            "stage": "pipeline",
            "reason": "item_processing_failed",
            "detail": first.get("status"),
        }
    return {
        "ok": True,
        "stage": "ingest",
        "reason": "parsed",
        "detail": first.get("status"),
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
    bypass_override: str | None = None,
    parser_override: str | None = None,
    ignore_schedule_gates: bool = False,
    operator_session_id: str | None = None,
) -> dict[str, Any]:
    try:
        summary = await runner.run_tenant(
            tenant_id,
            max_items=max_items,
            source_ids=[_run_source_id(source, requested_source_id)],
            bypass_override=bypass_override,
            parser_override=parser_override,
            ignore_schedule_gates=ignore_schedule_gates,
            operator_session_id=operator_session_id,
        )
    except OperatorSessionAttachError as exc:
        payload = dict(exc.payload)
        return {
            "ok": False,
            "status": str(payload.get("status") or "unavailable"),
            "executed": False,
            "error": str(payload.get("error") or "session_attach_failed"),
            "run": None,
            "session_id": operator_session_id,
            "session_attached": False,
        }
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
    result: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "executed": True,
        "error": None,
        "run": payload,
        "parse": _parse_diagnosis(run=payload),
    }
    if operator_session_id:
        result["session_id"] = operator_session_id
        result["session_attached"] = True
    return result


def _ladder_from_route(route: dict[str, Any], *, max_tier: str | None = None) -> list[str]:
    names: list[str] = []
    for raw in list(route.get("fallback_order") or []):
        name = _normalize_bypass_name(str(raw))
        if not name or name in names:
            continue
        names.append(name)
        if max_tier and name == _normalize_bypass_name(max_tier):
            break
    return names[:_SWEEP_MAX_ROUTES]


def _same_resolved_engine(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return _ENGINE_ALIASES.get(left.lower(), left) == _ENGINE_ALIASES.get(right.lower(), right)


def _default_escalation_ladder(runner: Any, *, max_tier: str | None = None) -> list[str]:
    getter = getattr(runner, "default_escalation_ladder", None)
    if not callable(getter):
        return []
    raw = getter()
    if not isinstance(raw, list | tuple):
        return []
    return _ladder_from_route({"fallback_order": list(raw)}, max_tier=max_tier)


def _escalation_ladder(
    runner: Any,
    route: dict[str, Any],
    *,
    max_tier: str | None = None,
) -> list[str]:
    ladder = _ladder_from_route(route, max_tier=max_tier)
    if ladder:
        return ladder
    return _default_escalation_ladder(runner, max_tier=max_tier)


async def _session_engine_name(runner: Any, session_id: str | None) -> str | None:
    if not session_id:
        return None
    getter = getattr(runner, "get_operator_browser_session", None)
    if not callable(getter):
        return None
    snap = await getter(session_id)
    if not isinstance(snap, dict):
        return None
    raw = snap.get("engine")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _ENGINE_ALIASES.get(raw.strip().lower(), raw.strip())


async def _attempt_bypass(
    runner: Any,
    *,
    tenant_id: str,
    source: dict[str, Any],
    source_id: str,
    bypass: str,
    max_items: int,
    parser_override: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    listing_url = _listing_url(source, None)
    browser = bypass in _BROWSER_ENGINES
    probe_fn = getattr(runner, "probe_browser_listing", None)
    if browser and listing_url and callable(probe_fn) and not session_id:
        listing = await probe_fn(
            tenant_id,
            url=listing_url,
            engine=bypass,
            headed=False,
            max_items=max_items,
        )
        if not isinstance(listing, dict):
            listing = {"status": "error", "error": "invalid_probe_payload", "executed": False}
        parse = _parse_diagnosis(listing=listing)
        return {
            "bypass": bypass,
            "kind": "browser",
            "ok": bool(listing.get("ok")),
            "status": listing.get("status"),
            "executed": bool(listing.get("executed")),
            "error": listing.get("error"),
            "challenge": listing.get("challenge"),
            "item_count": listing.get("item_count"),
            "items": listing.get("items") or [],
            "page_title": listing.get("page_title"),
            "parse": parse,
            "run": None,
        }
    executed = await _execute_source_run(
        runner,
        tenant_id=tenant_id,
        source=source,
        requested_source_id=source_id,
        max_items=max_items,
        bypass_override=bypass,
        parser_override=parser_override,
        ignore_schedule_gates=True,
        operator_session_id=session_id,
    )
    run = executed.get("run") if isinstance(executed.get("run"), dict) else {}
    parse = executed.get("parse") or _parse_diagnosis(run=run)
    return {
        "bypass": bypass,
        "kind": "ingest",
        "ok": bool(executed.get("ok")),
        "status": executed.get("status"),
        "executed": bool(executed.get("executed")),
        "error": executed.get("error"),
        "challenge": None,
        "item_count": None,
        "items": [],
        "page_title": None,
        "parse": parse,
        "run": run,
        **_session_fields(executed, session_id),
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


def _session_fields(executed: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {}
    attached = bool(executed.get("session_attached"))
    return {
        "session_id": executed.get("session_id") or session_id,
        "session_attached": attached,
    }


async def run_source(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    max_items: int | None = None,
    parser: str | None = None,
    bypass: str | None = None,
    session_id: str | None = None,
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
    pin = _normalize_bypass_name(requested_bypass)
    if pin is not None:
        registered = _registered_bypass_names()
        if registered and pin not in registered:
            route = await _route_payload(runner, tenant_id, source_id, bypass=pin)
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="unavailable",
                error="bypass_unavailable",
                notes=_notes(f"bypass {pin!r} is not registered in this runtime"),
                source=snapshot,
                route=route,
                selected_route=selected,
                requested_bypass=requested_bypass,
                requested_parser=requested_parser,
            )
        parser_pin = None
        if requested_parser and requested_parser not in {"auto"}:
            parser_error = _parser_pin_error(source, requested_parser)
            if parser_error:
                return _envelope(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    status="unavailable" if parser_error == "parser_unavailable" else "unsupported",
                    error=parser_error,
                    notes=_notes(f"cannot pin parser {requested_parser!r} for this source"),
                    source=snapshot,
                    route=route,
                    selected_route=selected,
                    requested_bypass=requested_bypass,
                    requested_parser=requested_parser,
                )
            parser_pin = requested_parser
        mismatch = _parser_host_mismatch(source, parser_pin) if parser_pin else None
        attempt = await _attempt_bypass(
            runner,
            tenant_id=tenant_id,
            source=source,
            source_id=source_id,
            bypass=pin,
            max_items=int(max_items) if max_items is not None else 5,
            parser_override=parser_pin,
            session_id=session_id,
        )
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status=str(attempt.get("status") or "error"),
            ok=bool(attempt.get("ok")),
            executed=bool(attempt.get("executed")),
            error=attempt.get("error"),
            notes=_notes(
                f"pinned bypass {pin} for this call only",
                "does not keep the pin on the tenant source",
                mismatch or "",
            ),
            source=snapshot,
            route=route,
            selected_route=selected,
            requested_bypass=requested_bypass,
            requested_parser=requested_parser,
            attempt=attempt,
            parse=attempt.get("parse"),
            run=attempt.get("run"),
            challenge=attempt.get("challenge"),
            item_count=attempt.get("item_count"),
            items=attempt.get("items") or [],
            browser_required=pin in _BROWSER_ENGINES,
            max_items=max_items,
            **_session_fields(attempt, session_id),
        )
    parser_pin = None
    if requested_parser and requested_parser not in {"auto"}:
        parser_error = _parser_pin_error(source, requested_parser)
        if parser_error:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="unavailable" if parser_error == "parser_unavailable" else "unsupported",
                error=parser_error,
                notes=_notes(
                    f"cannot pin parser {requested_parser!r} for this source",
                    "career_site pin accepts a registered monitor or scraper name",
                ),
                source=snapshot,
                route=route,
                selected_route=selected,
                requested_bypass=requested_bypass,
                requested_parser=requested_parser,
            )
        parser_pin = requested_parser
    mismatch = _parser_host_mismatch(source, parser_pin) if parser_pin else None
    executed = await _execute_source_run(
        runner,
        tenant_id=tenant_id,
        source=source,
        requested_source_id=source_id,
        max_items=max_items,
        parser_override=parser_pin,
        ignore_schedule_gates=True,
        operator_session_id=session_id,
    )
    parser_notes = (
        [f"pinned parser {parser_pin} for this call only"]
        if parser_pin
        else ["no parser pin: source keeps its configured monitor/scraper"]
    )
    if mismatch:
        parser_notes.append(mismatch)
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed["status"]),
        ok=bool(executed["ok"]),
        executed=bool(executed["executed"]),
        error=executed.get("error"),
        notes=_notes(
            "run_source is a source-scoped TenantRunner ingest",
            "no bypass pin: source uses the standard adaptive ladder",
            *parser_notes,
        ),
        source=snapshot,
        route=route,
        selected_route=selected,
        requested_bypass=requested_bypass,
        requested_parser=requested_parser,
        parser_host_mismatch=bool(mismatch),
        parse=executed.get("parse"),
        run=executed.get("run"),
        browser_required=_is_browser_route(route, source),
        max_items=max_items,
        **_session_fields(executed, session_id),
    )


async def run_source_escalation(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    strategy: str = "recommended",
    max_tier: str | None = None,
    max_items: int = 5,
    session_id: str | None = None,
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
    if normalized == "all":
        source = await _find_source(runner, tenant_id, source_id)
        if source is None:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="source_not_found",
                error="source_not_found",
                strategy=normalized,
                max_tier=max_tier,
            )
        if source.get("enabled") is False:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="source_disabled",
                error="source_disabled",
                strategy=normalized,
                source=_source_snapshot(source),
                max_tier=max_tier,
            )
        route = await _route_payload(runner, tenant_id, source_id)
        ladder = _escalation_ladder(runner, route, max_tier=max_tier)
        if not ladder:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="unsupported",
                error="empty_escalation_ladder",
                notes=_notes("source route has no fallback_order to walk"),
                strategy=normalized,
                max_tier=max_tier,
                source=_source_snapshot(source),
                route=route,
                selected_route=_selected_route(route),
                escalation_ladder=[],
                attempts=[],
                browser_required=_is_browser_route(route, source),
                session_id=session_id,
                session_attached=False,
            )
        session_engine = await _session_engine_name(runner, session_id)
        registered = _registered_bypass_names()
        attempts: list[dict[str, Any]] = []
        for name in ladder:
            if registered and name not in registered:
                attempts.append(
                    {
                        "bypass": name,
                        "kind": "skipped",
                        "ok": False,
                        "status": "unavailable",
                        "executed": False,
                        "error": "bypass_unavailable",
                        "parse": {
                            "ok": False,
                            "stage": "setup",
                            "reason": "engine_unavailable",
                            "detail": None,
                        },
                    }
                )
                continue
            attach = session_id if _same_resolved_engine(name, session_engine) else None
            attempts.append(
                await _attempt_bypass(
                    runner,
                    tenant_id=tenant_id,
                    source=source,
                    source_id=source_id,
                    bypass=name,
                    max_items=int(max_items),
                    session_id=attach,
                )
            )
        worked = [item for item in attempts if item.get("ok") and item.get("parse", {}).get("ok")]
        status = "ok" if worked else "degraded"
        attached_used = any(bool(item.get("session_attached")) for item in attempts)
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status=status,
            ok=bool(worked),
            executed=any(bool(item.get("executed")) for item in attempts),
            error=None if worked else "no_working_bypass",
            notes=_notes(
                "strategy=all walks fallback_order with a bounded per-route probe",
                "browser routes use listing probe; others pin ingest for this call",
                "session engine reuses the open operator tab; other tiers do not",
            ),
            strategy=normalized,
            max_tier=max_tier,
            source=_source_snapshot(source),
            route=route,
            selected_route=_selected_route(route),
            escalation_ladder=ladder,
            session_id=session_id,
            session_attached=attached_used,
            attempts=attempts,
            working_bypass=[item.get("bypass") for item in worked],
            browser_required=any(name in _BROWSER_ENGINES for name in ladder),
        )
    result = await run_source(
        runner,
        tenant_id=tenant_id,
        source_id=source_id,
        max_items=int(max_items),
        session_id=session_id,
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
    pin = _normalize_bypass_name(requested) or requested
    registered = _registered_bypass_names()
    if registered and pin not in registered and pin not in _ADAPTIVE_BYPASS:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unavailable",
            error="bypass_unavailable",
            notes=_notes("requested bypass is not registered in this runtime"),
            extra=diagnosed,
        )
    if pin in _ADAPTIVE_BYPASS or requested in _ADAPTIVE_BYPASS:
        executed = await _execute_source_run(
            runner,
            tenant_id=tenant_id,
            source=source,
            requested_source_id=source_id,
            max_items=int(max_items),
            ignore_schedule_gates=True,
        )
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status=str(executed["status"]),
            ok=bool(executed["ok"]),
            executed=bool(executed["executed"]),
            error=executed.get("error"),
            notes=_notes("probe executed the standard adaptive ladder"),
            extra={**diagnosed, "parse": executed.get("parse"), "run": executed.get("run")},
        )
    attempt = await _attempt_bypass(
        runner,
        tenant_id=tenant_id,
        source=source,
        source_id=source_id,
        bypass=pin,
        max_items=int(max_items),
    )
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(attempt.get("status") or "error"),
        ok=bool(attempt.get("ok")),
        executed=bool(attempt.get("executed")),
        error=attempt.get("error"),
        notes=_notes(
            f"probed bypass {pin}",
            "browser routes use listing probe; others pin ingest for this call",
        ),
        extra={
            **diagnosed,
            "attempt": attempt,
            "parse": attempt.get("parse"),
            "run": attempt.get("run"),
            "challenge": attempt.get("challenge"),
            "item_count": attempt.get("item_count"),
            "items": attempt.get("items") or [],
        },
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
    solve: str = "none",
) -> dict[str, Any]:
    normalized_probe = (probe or "listing").strip().lower()
    normalized_engine = (engine or "auto").strip().lower()
    resolved_engine = _resolve_listing_engine(normalized_engine)
    if max_items is None or int(max_items) <= 0:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="invalid_max_items",
            probe=normalized_probe,
            engine=normalized_engine,
        )
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
        if source.get("enabled") is False:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="source_disabled",
                error="source_disabled",
                probe=normalized_probe,
                engine=normalized_engine,
                requested_bypass=bypass,
                source=_source_snapshot(source),
                route=route,
                selected_route=_selected_route(route),
            )
    if normalized_probe not in _LIVE_PROBES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="live_browser_probe_not_implemented",
            missing_service=_MISSING_BROWSER_PROBE,
            notes=_notes(
                f"probe={normalized_probe} is not implemented yet",
                "listing/detail/challenge are the live paths",
            ),
            probe=normalized_probe,
            engine=normalized_engine,
            requested_bypass=bypass,
            source=None if source is None else _source_snapshot(source),
            route=route,
            selected_route=None if route is None else _selected_route(route),
        )
    listing_url = _listing_url(source, url)
    if listing_url is None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="listing_url_required",
            notes=_notes(
                f"{normalized_probe} probe needs an http(s) url argument or a source spec url",
                "local_fixture / telegram sources have no listing page",
            ),
            probe=normalized_probe,
            engine=normalized_engine,
            requested_bypass=bypass,
            source=None if source is None else _source_snapshot(source),
            route=route,
            selected_route=None if route is None else _selected_route(route),
        )
    probe_fn = getattr(runner, "probe_browser_listing", None)
    if not callable(probe_fn):
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="live_browser_probe_not_implemented",
            missing_service=_MISSING_BROWSER_PROBE,
            notes=_notes("runner has no probe_browser_listing port"),
            probe=normalized_probe,
            engine=normalized_engine,
            requested_bypass=bypass,
            requested_url=listing_url,
            source=None if source is None else _source_snapshot(source),
            route=route,
            selected_route=None if route is None else _selected_route(route),
        )
    spec_raw = (source or {}).get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    bypass_config = spec.get("bypass_config")
    url_filter = spec.get("url_filter")
    executed = await probe_fn(
        tenant_id,
        url=listing_url,
        engine=resolved_engine,
        headed=headed,
        max_items=int(max_items),
        bypass_config=bypass_config if isinstance(bypass_config, dict) else None,
        probe=normalized_probe,
        solve=(solve or "none").strip().lower() or "none",
        url_filter=url_filter,
    )
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_probe_payload", "executed": False}
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(
            *list(executed.get("notes") or []),
            f"{normalized_probe} probe does not run TenantRunner ingest",
        ),
        probe=normalized_probe,
        engine=executed.get("engine") or resolved_engine,
        requested_bypass=bypass,
        requested_url=listing_url,
        final_url=executed.get("final_url"),
        page_title=executed.get("page_title"),
        heading=executed.get("heading"),
        text_preview=executed.get("text_preview"),
        challenge=executed.get("challenge"),
        captcha=executed.get("captcha"),
        solve=executed.get("solve"),
        item_count=executed.get("item_count"),
        items=executed.get("items") or [],
        fingerprint=executed.get("fingerprint"),
        user_agent=executed.get("user_agent"),
        source=None if source is None else _source_snapshot(source),
        route=route,
        selected_route=None if route is None else _selected_route(route),
    )


def _session_url(source: dict[str, Any] | None, url: str | None) -> str | None:
    return _listing_url(source, url)


async def open_browser_session(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str | None = None,
    url: str | None = None,
    engine: str = "auto",
    headed: bool = True,
    bypass: str | None = None,
    profile: str = "ephemeral",
    manual_challenge: bool = False,
) -> dict[str, Any]:
    normalized_engine = (engine or "auto").strip().lower()
    normalized_profile = (profile or "ephemeral").strip().lower() or "ephemeral"
    if normalized_engine not in BROWSER_ENGINES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_engine",
            engine=engine,
            profile=normalized_profile,
        )
    if normalized_profile not in _SESSION_PROFILES:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="unsupported_profile",
            engine=normalized_engine,
            profile=profile,
        )
    source = None
    route = None
    if source_id:
        source = await _find_source(runner, tenant_id, source_id)
        if source is None:
            return _envelope(
                tenant_id=tenant_id,
                source_id=source_id,
                status="source_not_found",
                error="source_not_found",
                engine=normalized_engine,
                profile=normalized_profile,
            )
        route = await _route_payload(runner, tenant_id, source_id, bypass=bypass)
    target = _session_url(source, url)
    if target is None:
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="unsupported",
            error="listing_url_required",
            notes=_notes("open_browser_session needs an http(s) url or a source spec url"),
            engine=normalized_engine,
            profile=normalized_profile,
            source=None if source is None else _source_snapshot(source),
            route=route,
        )
    open_fn = getattr(runner, "open_operator_browser_session", None)
    if not callable(open_fn):
        return _envelope(
            tenant_id=tenant_id,
            source_id=source_id,
            status="not_implemented",
            error="live_browser_session_not_implemented",
            missing_service="operator_browser_session",
            engine=normalized_engine,
            profile=normalized_profile,
        )
    spec_raw = (source or {}).get("spec")
    spec: dict[str, Any] = spec_raw if isinstance(spec_raw, dict) else {}
    executed = await open_fn(
        tenant_id,
        url=target,
        engine=_resolve_listing_engine(normalized_engine),
        headed=headed or manual_challenge,
        bypass_config=spec.get("bypass_config")
        if isinstance(spec.get("bypass_config"), dict)
        else None,
        manual_challenge=manual_challenge,
        profile=normalized_profile,
    )
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_session_payload"}
    return _envelope(
        tenant_id=tenant_id,
        source_id=source_id,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(*list(executed.get("notes") or [])),
        extra={
            k: v
            for k, v in executed.items()
            if k not in {"ok", "status", "executed", "error", "notes"}
        },
        source=None if source is None else _source_snapshot(source),
        route=route,
        engine=executed.get("engine") or _resolve_listing_engine(normalized_engine),
        profile=normalized_profile,
        requested_url=target,
        browser_required=True,
    )


async def get_browser_session(runner: Any, *, session_id: str) -> dict[str, Any]:
    get_fn = getattr(runner, "get_operator_browser_session", None)
    if not callable(get_fn):
        return _envelope(
            tenant_id=None,
            source_id=None,
            status="not_implemented",
            error="live_browser_session_not_implemented",
            missing_service="operator_browser_session",
        )
    executed = await get_fn(session_id)
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_session_payload"}
    return _envelope(
        tenant_id=executed.get("tenant_id"),
        source_id=None,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(*list(executed.get("notes") or [])),
        extra={
            k: v
            for k, v in executed.items()
            if k not in {"ok", "status", "executed", "error", "notes"}
        },
    )


async def continue_browser_session(
    runner: Any,
    *,
    session_id: str,
    instruction: str | None = None,
) -> dict[str, Any]:
    cont_fn = getattr(runner, "continue_operator_browser_session", None)
    if not callable(cont_fn):
        return _envelope(
            tenant_id=None,
            source_id=None,
            status="not_implemented",
            error="live_browser_session_not_implemented",
            missing_service="operator_browser_session",
        )
    executed = await cont_fn(session_id, instruction)
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_session_payload"}
    return _envelope(
        tenant_id=executed.get("tenant_id"),
        source_id=None,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(*list(executed.get("notes") or [])),
        extra={
            k: v
            for k, v in executed.items()
            if k not in {"ok", "status", "executed", "error", "notes"}
        },
    )


async def capture_browser_artifact(
    runner: Any,
    *,
    session_id: str,
    artifact_type: str = "text",
) -> dict[str, Any]:
    cap_fn = getattr(runner, "capture_operator_browser_artifact", None)
    if not callable(cap_fn):
        return _envelope(
            tenant_id=None,
            source_id=None,
            status="not_implemented",
            error="live_browser_session_not_implemented",
            missing_service="operator_browser_session",
        )
    executed = await cap_fn(session_id, artifact_type)
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_session_payload"}
    return _envelope(
        tenant_id=executed.get("tenant_id"),
        source_id=None,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(*list(executed.get("notes") or [])),
        extra={
            k: v
            for k, v in executed.items()
            if k not in {"ok", "status", "executed", "error", "notes"}
        },
    )


async def close_browser_session(runner: Any, *, session_id: str) -> dict[str, Any]:
    close_fn = getattr(runner, "close_operator_browser_session", None)
    if not callable(close_fn):
        return _envelope(
            tenant_id=None,
            source_id=None,
            status="not_implemented",
            error="live_browser_session_not_implemented",
            missing_service="operator_browser_session",
        )
    executed = await close_fn(session_id)
    if not isinstance(executed, dict):
        executed = {"status": "error", "error": "invalid_session_payload"}
    return _envelope(
        tenant_id=executed.get("tenant_id"),
        source_id=None,
        status=str(executed.get("status") or "error"),
        ok=bool(executed.get("ok")),
        executed=bool(executed.get("executed")),
        error=executed.get("error"),
        notes=_notes(*list(executed.get("notes") or [])),
        extra={
            k: v
            for k, v in executed.items()
            if k not in {"ok", "status", "executed", "error", "notes"}
        },
    )
