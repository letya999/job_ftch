"""Product-oriented MCP helpers (bot parity: shots, sources, filtered search).

Keeps TenantMCPServer thin: orchestration stays in application layer
(TenantRunner, shot_sync, profile_inputs).
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal, cast

from job_ftch.application.profile_inputs import (
    list_examples,
    remove_example_from_profile,
)
from job_ftch.application.profile_parsing import build_candidate_profile_from_payload
from job_ftch.application.resume_extraction import add_example_to_profile
from job_ftch.application.shot_sync import add_shot_async, remove_shot_async
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.domain import ManagedCandidateProfile

SurfaceName = Literal["core", "ops", "admin"]

ShotKind = Literal["resume", "job"]
ShotPolarity = Literal["positive", "negative"]

_DEFAULT_USER = "mcp"
_DEFAULT_PROFILE = "mcp_default"


def resolve_surface() -> SurfaceName:
    raw = os.environ.get("JOB_FTCH_MCP_SURFACE", "core").strip().lower()
    if raw in {"core", "ops", "admin"}:
        return raw  # type: ignore[return-value]
    return "core"


def example_kind(polarity: ShotPolarity, kind: ShotKind) -> str:
    if kind == "job":
        return "positive_job" if polarity == "positive" else "negative_job"
    return "positive_resume" if polarity == "positive" else "negative_resume"


def shot_role(*, user_id: str, tenant_id: str, kind: ShotKind, polarity: ShotPolarity) -> str:
    bucket = "positive" if polarity == "positive" else "negative"
    family = "vacancy" if kind == "job" else "resume"
    return f"user:{user_id}@tenant:{tenant_id}:{family}:{bucket}"


async def ensure_managed_profile(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str | None = None,
) -> ManagedCandidateProfile:
    """Load active profile or create a default managed profile for MCP shots."""
    profiles = await runner.list_candidate_profiles(tenant_id, user_id)
    if profile_id is None:
        active = next((p for p in profiles if p.get("active")), None)
        if active is not None:
            profile_id = str(active["profile_id"])
        elif profiles:
            profile_id = str(profiles[0]["profile_id"])
        else:
            profile_id = _DEFAULT_PROFILE
    existing = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if existing is not None:
        return cast("ManagedCandidateProfile", existing)
    profile = build_candidate_profile_from_payload(
        user_id=user_id,
        profile_id=profile_id,
        payload={"summary": "", "name": profile_id},
    )
    managed = ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=profile,
        updated_at=datetime.now(UTC),
    )
    await runner.save_and_activate_candidate_profile(tenant_id, managed)
    loaded = await runner.get_candidate_profile(tenant_id, user_id, profile_id)
    if loaded is None:
        msg = f"Failed to create profile {profile_id!r} for user {user_id!r}"
        raise RuntimeError(msg)
    return cast("ManagedCandidateProfile", loaded)


async def add_shots(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str = _DEFAULT_USER,
    polarity: ShotPolarity,
    kind: ShotKind,
    text: str | None = None,
    texts: list[str] | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Add one or many positive/negative resume/job shots (bot parity)."""
    items: list[str] = []
    if text and text.strip():
        items.append(text.strip())
    if texts:
        items.extend(t.strip() for t in texts if t and t.strip())
    if not items:
        msg = "Provide non-empty text or texts[]"
        raise ValueError(msg)

    managed = await ensure_managed_profile(
        runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
    )
    ekind = example_kind(polarity, kind)
    role = shot_role(user_id=user_id, tenant_id=tenant_id, kind=kind, polarity=polarity)
    label = polarity
    sync_errors: list[str] = []

    for item in items:
        managed = add_example_to_profile(managed, item, kind=ekind)
        try:
            await remove_shot_async(text=item, role=role)
            await add_shot_async(
                text=item,
                label=label,
                role=role,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001 - profile still persisted
            sync_errors.append(f"{type(exc).__name__}: {exc}")

    await runner.save_and_activate_candidate_profile(tenant_id, managed)
    examples = list_examples(managed)
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": managed.profile_id,
        "polarity": polarity,
        "kind": kind,
        "added": len(items),
        "counts": {k: len(v) for k, v in examples.items()},
        "shot_sync_errors": sync_errors,
    }


async def list_shots(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str = _DEFAULT_USER,
    profile_id: str | None = None,
) -> dict[str, Any]:
    managed = await ensure_managed_profile(
        runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
    )
    examples = list_examples(managed)
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": managed.profile_id,
        "examples": examples,
        "counts": {k: len(v) for k, v in examples.items()},
    }


async def remove_shot(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str = _DEFAULT_USER,
    polarity: ShotPolarity,
    kind: ShotKind,
    index: int,
    profile_id: str | None = None,
) -> dict[str, Any]:
    managed = await ensure_managed_profile(
        runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
    )
    ekind = example_kind(polarity, kind)
    examples_before = list_examples(managed)
    if ekind == "positive_job":
        key = "positive_job"
    elif ekind == "negative_job":
        key = "negative_job"
    elif ekind.startswith("negative"):
        key = "negative_resume"
    else:
        key = "positive_resume"
    texts = examples_before.get(key, [])
    if index < 0 or index >= len(texts):
        msg = f"index {index} out of range for {key} (len={len(texts)})"
        raise ValueError(msg)
    removed_text = texts[index]
    managed = remove_example_from_profile(managed, ekind, index)
    role = shot_role(user_id=user_id, tenant_id=tenant_id, kind=kind, polarity=polarity)
    sync_error = None
    try:
        await remove_shot_async(text=removed_text, role=role)
    except Exception as exc:  # noqa: BLE001
        sync_error = f"{type(exc).__name__}: {exc}"
    await runner.save_and_activate_candidate_profile(tenant_id, managed)
    examples = list_examples(managed)
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": managed.profile_id,
        "removed_index": index,
        "removed_preview": removed_text[:200],
        "counts": {k: len(v) for k, v in examples.items()},
        "shot_sync_error": sync_error,
    }


async def upsert_source(
    runner: Any,
    *,
    tenant_id: str,
    link: str,
    source_type: str | None = None,
    limit: int = 100,
    replace_source_id: str | None = None,
) -> dict[str, Any]:
    """Add a source, or replace an existing one (change)."""
    if replace_source_id:
        with suppress(KeyError):
            await runner.set_source_enabled(tenant_id, replace_source_id, enabled=False)
    runtime = runner.get_runtime(tenant_id)
    spec = await build_source_spec_from_input(
        link,
        auth_provider=runtime.auth_provider,
        source_type=source_type,
        limit=limit,
    )
    payload = await runner.add_source_spec(
        tenant_id,
        spec,
        added_via="mcp",
        input_value=link,
    )
    return {
        "action": "replaced" if replace_source_id else "added",
        "replaced_source_id": replace_source_id,
        "source": payload,
    }


def filter_job_groups(
    groups: list[Any],
    *,
    limit: int,
    company: str | None = None,
    location: str | None = None,
    work_mode: str | None = None,
    language: str | None = None,
    source_name: str | None = None,
    min_score: float | None = None,
    routing_decision: str | None = None,
) -> list[dict[str, Any]]:
    """Post-filter search results (backend search is query-only today)."""

    def _contains(hay: object | None, needle: str) -> bool:
        if hay is None:
            return False
        return needle.casefold() in str(hay).casefold()

    out: list[dict[str, Any]] = []
    for group in groups:
        job = group.canonical_job
        if company and not _contains(job.company or job.company_canonical, company):
            continue
        if location and not _contains(job.location, location):
            continue
        if work_mode:
            mode_val = getattr(job.work_mode, "value", job.work_mode)
            if str(mode_val).casefold() != work_mode.casefold():
                continue
        if language:
            lang_val = getattr(job.language, "value", job.language)
            if str(lang_val).casefold() != language.casefold():
                continue
        if source_name and not any(
            _contains(getattr(j, "source_name", None), source_name) for j in group.jobs
        ):
            continue
        if min_score is not None:
            score = job.best_score
            if score is None or float(score) < min_score:
                continue
        if routing_decision is not None:
            decision = getattr(job.routing_decision, "value", job.routing_decision)
            if str(decision or "").casefold() != routing_decision.casefold():
                continue
        out.append(group.model_dump(mode="json"))
        if len(out) >= limit:
            break
    return out
