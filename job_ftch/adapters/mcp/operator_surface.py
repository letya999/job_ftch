"""Operator MCP helpers wrapping TenantRunner and application services."""

from __future__ import annotations

from typing import Any, cast

from job_ftch.adapters.mcp import product_surface as mcp_examples
from job_ftch.application.vacancy_feedback import (
    build_feedback,
    clear_feedback,
    get_feedback_audience,
    load_feedback,
    promotable_texts,
    record_feedback,
    set_feedback_audience,
    summarize_feedback,
)
from job_ftch.domain.feedback import FeedbackAudience, FeedbackVerdict

_FEEDBACK_VERDICTS = {"not_profile": FeedbackVerdict.OFF_PROFILE}
_AUDIENCES = {item.value for item in FeedbackAudience}
_BYPASS_GOALS = frozenset({"listing", "detail", "challenge"})
_ARTIFACT_TYPES = frozenset({"summary", "html", "screenshot", "trace", "raw"})
_PROMOTION_THRESHOLD = 2


def _error(code: str, message: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": code, "message": message}
    payload.update(extra)
    return payload


def _store(runner: Any, tenant_id: str) -> Any:
    return runner.get_runtime(tenant_id).store


async def list_feedback(
    runner: Any,
    *,
    tenant_id: str,
    audience: str | None = None,
) -> dict[str, Any]:
    store = _store(runner, tenant_id)
    current = await get_feedback_audience(store, tenant_id)
    records = await load_feedback(store, tenant_id)
    summary = summarize_feedback(tenant_id, records)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "audience": current.value,
        "summary": summary.model_dump(mode="json"),
        "records": [record.model_dump(mode="json") for record in records],
    }
    if audience is not None:
        payload["requested_audience"] = audience
    return payload


async def add_vacancy_feedback(
    runner: Any,
    *,
    tenant_id: str,
    job_id: str,
    user_id: str,
    verdict: str = "not_profile",
    note: str | None = None,
) -> dict[str, Any]:
    mapped = _FEEDBACK_VERDICTS.get(verdict)
    if mapped is None:
        return _error(
            "invalid_arguments",
            "verdict must be not_profile",
            verdict=verdict,
        )
    job = await runner.get_job(job_id, tenant_id=tenant_id)
    title = ""
    url = ""
    source_name = ""
    excerpt = ""
    if job is not None:
        title = str(job.title or "")
        url = str(job.canonical_url or job.source_url or "")
        source_name = str(job.source_name or "")
        excerpt = str(job.description or job.title or "")
    if note and note.strip():
        excerpt = f"{excerpt}\n{note.strip()}".strip() if excerpt else note.strip()
    feedback = build_feedback(
        tenant_id=tenant_id,
        job_id=job_id,
        user_id=user_id,
        title=title,
        url=url,
        source_name=source_name,
        excerpt=excerpt,
    )
    if feedback.verdict is not mapped:
        feedback = feedback.model_copy(update={"verdict": mapped})
    stored, records = await record_feedback(_store(runner, tenant_id), feedback)
    summary = summarize_feedback(tenant_id, records)
    return {
        "tenant_id": tenant_id,
        "stored": stored,
        "job_id": job_id,
        "user_id": user_id,
        "verdict": mapped.value,
        "summary": summary.model_dump(mode="json"),
    }


async def set_operator_feedback_audience(
    runner: Any,
    *,
    tenant_id: str,
    audience: str,
) -> dict[str, Any]:
    if audience not in _AUDIENCES:
        return _error(
            "invalid_arguments",
            "audience must be one of off|admin|all",
            audience=audience,
        )
    value = FeedbackAudience(audience)
    await set_feedback_audience(_store(runner, tenant_id), tenant_id, value)
    return {"tenant_id": tenant_id, "audience": value.value}


async def clear_operator_feedback(runner: Any, *, tenant_id: str) -> dict[str, Any]:
    removed = await clear_feedback(_store(runner, tenant_id), tenant_id)
    return {"tenant_id": tenant_id, "removed": removed}


async def promote_feedback_to_example(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str | None = None,
    feedback_id: str | None = None,
    job_id: str | None = None,
    label: str = "negative",
) -> dict[str, Any]:
    if label != "negative":
        return _error(
            "invalid_arguments",
            "label must be negative",
            label=label,
        )
    store = _store(runner, tenant_id)
    records = await load_feedback(store, tenant_id)
    summary = summarize_feedback(tenant_id, records)
    named_job_id = job_id or feedback_id
    texts: list[str] = []
    if named_job_id:
        named = next((item for item in records if item.job_id == named_job_id), None)
        excerpt = named.excerpt if named is not None else ""
        if not excerpt:
            job = await runner.get_job(named_job_id, tenant_id=tenant_id)
            if job is not None:
                excerpt = str(job.description or job.title or "")
        if excerpt.strip():
            texts.append(excerpt.strip())
    else:
        texts.extend(promotable_texts(summary, threshold=_PROMOTION_THRESHOLD))

    if not texts:
        return {
            "status": "skipped",
            "reason": "threshold_not_met",
            "threshold": _PROMOTION_THRESHOLD,
            "promoted": 0,
            "tenant_id": tenant_id,
            "job_id": named_job_id,
        }

    added = await mcp_examples.add_operator_example(
        runner,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="vacancy",
        label="negative",
        texts=texts,
        profile_id=profile_id,
        refresh_policy="auto",
    )
    return {
        "status": "promoted",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "promoted": len(texts),
        "job_id": named_job_id,
        "example": added,
    }


async def compile_examples_ontology(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    from job_ftch.application.ontology_enrichment import compile_profile_ontology

    managed = await mcp_examples.ensure_managed_profile(
        runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
    )
    runtime = runner.get_runtime(tenant_id)
    ontology_store = getattr(runtime, "ontology_store", None)
    llm = getattr(runtime, "llm_provider", None)
    compiled = await compile_profile_ontology(
        managed,
        llm=llm,
        ontology_store=ontology_store,
        persist=not dry_run,
    )
    raw_errors = compiled.get("ontology_errors")
    errors = [str(item) for item in raw_errors] if isinstance(raw_errors, list) else []
    if ontology_store is None and "ontology_store_missing" not in errors:
        errors.append("ontology_store_missing")
    raw_pos = compiled.get("pos_added")
    pos_added = raw_pos if isinstance(raw_pos, int) else 0
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": managed.profile_id,
        "dry_run": dry_run,
        "force": force,
        "persisted": bool(compiled.get("persisted", not dry_run and ontology_store is not None)),
        "pos_added": pos_added,
        "model": compiled.get("model"),
        "ontology_errors": errors,
        "ontology_store": ontology_store is not None,
    }


async def recommend_bypass_route(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    goal: str = "listing",
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if goal not in _BYPASS_GOALS:
        return _error(
            "invalid_arguments",
            "goal must be one of listing|detail|challenge",
            goal=goal,
        )
    from job_ftch.application.browser_capability_inventory import explanation_to_public_dict

    explanation = explanation_to_public_dict(
        await runner.explain_browser_route(tenant_id, source_id)
    )
    sources = await runner.list_sources(tenant_id)
    source = next((item for item in sources if item.get("source_id") == source_id), None)
    selected_id = explanation.get("selected_capability_id")
    diagnostics = explanation.get("diagnostics") or []
    selected = next(
        (
            item
            for item in diagnostics
            if isinstance(item, dict) and item.get("capability_id") == selected_id
        ),
        None,
    )
    if selected is None and diagnostics:
        selected = diagnostics[0] if isinstance(diagnostics[0], dict) else None
    requirements = source.get("requirements") if isinstance(source, dict) else None
    hint = None
    if isinstance(requirements, dict):
        hint = requirements.get("browser_setup_hint")
    if not hint:
        hint = "no extra setup required for the selected route"
    return {
        "tenant_id": tenant_id,
        "source_id": source_id,
        "goal": goal,
        "constraints": constraints,
        "recommended_route": selected_id,
        "selected": selected,
        "risk": (selected or {}).get("risk") if isinstance(selected, dict) else None,
        "setup_hint": hint,
        "setup": {
            "goal": goal,
            "manual_steps": [hint],
            "notes": ["recommendation only; this tool does not start a browser"],
        },
        "explanation": explanation,
        "live_browser": False,
    }


async def explain_source_failure(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    sources = await runner.list_sources(tenant_id)
    source = next((item for item in sources if item.get("source_id") == source_id), None)
    if source is None:
        return {
            "status": "unavailable",
            "error": "source_not_found",
            "tenant_id": tenant_id,
            "source_id": source_id,
            "run_id": run_id,
        }
    parse = source.get("parser") if isinstance(source.get("parser"), dict) else None
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "source_id": source_id,
        "run_id": run_id,
        "health": {
            "status": source.get("status"),
            "degraded": source.get("degraded"),
            "failure_streak": source.get("failure_streak"),
            "last_error": source.get("last_error"),
            "last_failed": source.get("last_failed"),
        },
        "diagnostics": {
            "recommended_route": source.get("recommended_route"),
            "assessment": source.get("assessment") or source.get("source_assessment"),
            "freshness": source.get("freshness"),
            "bypass": source.get("bypass"),
            "parser": parse or source.get("parser"),
        },
        "probed": False,
    }


async def get_source_artifacts(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    run_id: str | None = None,
    artifact_type: str = "summary",
) -> dict[str, Any]:
    if artifact_type not in _ARTIFACT_TYPES:
        return _error(
            "invalid_arguments",
            "artifact_type must be one of summary|html|screenshot|trace|raw",
            artifact_type=artifact_type,
        )
    if artifact_type != "summary":
        return {
            "status": "unavailable",
            "error": "artifact_type_not_stored",
            "tenant_id": tenant_id,
            "source_id": source_id,
            "run_id": run_id,
            "artifact_type": artifact_type,
            "hint": "use capture_browser_artifact for session screenshots/traces",
        }
    store = _store(runner, tenant_id)
    getter = getattr(store, "get_last_run_snapshot_hashes", None)
    if not callable(getter):
        return {
            "status": "unavailable",
            "error": "snapshot_api_missing",
            "tenant_id": tenant_id,
            "source_id": source_id,
            "run_id": run_id,
            "artifact_type": artifact_type,
        }
    try:
        hashes = await getter(tenant_id, source_id)
    except Exception as exc:  # noqa: BLE001 - structured operator result
        return {
            "status": "unavailable",
            "error": f"{type(exc).__name__}",
            "tenant_id": tenant_id,
            "source_id": source_id,
            "run_id": run_id,
            "artifact_type": artifact_type,
        }
    if not hashes:
        return {
            "status": "unavailable",
            "error": "snapshot_missing",
            "tenant_id": tenant_id,
            "source_id": source_id,
            "run_id": run_id,
            "artifact_type": artifact_type,
        }
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "source_id": source_id,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "stable_id_count": len(hashes),
        "stable_ids": sorted(hashes)[:50],
    }


async def remove_source(runner: Any, *, tenant_id: str, source_id: str) -> dict[str, Any]:
    try:
        result = await runner.remove_source(tenant_id, source_id)
    except KeyError:
        return _error("unknown_source", f"Unknown source_id: {source_id}", source_id=source_id)
    if isinstance(result, dict):
        return cast("dict[str, Any]", result)
    return _error("unknown_source", f"Unknown source_id: {source_id}", source_id=source_id)


async def update_source(
    runner: Any,
    *,
    tenant_id: str,
    source_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return _error("invalid_arguments", "patch must be an object", source_id=source_id)
    try:
        result = await runner.update_source(tenant_id, source_id, patch)
    except KeyError:
        return _error("unknown_source", f"Unknown source_id: {source_id}", source_id=source_id)
    if not isinstance(result, dict):
        return _error("unknown_source", f"Unknown source_id: {source_id}", source_id=source_id)
    payload = cast("dict[str, Any]", result)
    if payload.get("error") or payload.get("status") in {
        "unsupported",
        "invalid",
        "invalid_arguments",
    }:
        return payload
    enabled = payload.get("enabled")
    spec = payload.get("spec")
    wrapped: dict[str, Any] = {
        "status": "disabled" if enabled is False else "updated",
        "source_id": payload.get("source_id", source_id),
        "enabled": enabled,
        "source": payload,
    }
    if isinstance(spec, dict):
        wrapped["spec"] = spec
        wrapped["limit"] = spec.get("limit")
    else:
        wrapped["limit"] = payload.get("limit")
    return wrapped
