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
ExampleKind = Literal["resume", "vacancy"]
ExampleLabel = Literal["positive", "negative"]

_DEFAULT_USER = "mcp"
_DEFAULT_PROFILE = "mcp_default"
_EXAMPLE_KINDS = frozenset({"resume", "vacancy"})
_EXAMPLE_LABELS = frozenset({"positive", "negative"})
_EXAMPLE_CLEAR_KINDS = frozenset({"all", "resume", "vacancy"})
_REFRESH_POLICIES = frozenset({"auto", "defer", "sync"})
_PUBLIC_EXAMPLE_KEYS = {
    "positive_resume": "positive_resume",
    "negative_resume": "negative_resume",
    "positive_job": "positive_vacancy",
    "negative_job": "negative_vacancy",
}


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


def _strip_shell_noise(text: str) -> str:
    """Drop accidental shell/Codex exec wrappers from pasted shot bodies."""
    cleaned = text.strip()
    if cleaned.startswith("Exit code:"):
        marker = "\nOutput:\n"
        idx = cleaned.find(marker)
        if idx >= 0:
            cleaned = cleaned[idx + len(marker) :].strip()
    return cleaned


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
    """Add one or many positive/negative resume/job shots (bot parity).

    Persists examples first, then compiles ontology once from the full
    profile (Telegram rebuild), not once per shot.
    """
    items: list[str] = []
    if text and text.strip():
        items.append(_strip_shell_noise(text))
    if texts:
        items.extend(_strip_shell_noise(t) for t in texts if t and t.strip())
    items = [item for item in items if item]
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
    ontology_errors: list[str] = []
    runtime = runner.get_runtime(tenant_id)

    from job_ftch.application.ontology_enrichment import compile_profile_ontology
    from job_ftch.application.resume_extraction import add_example_to_profile

    ontology_store = getattr(runtime, "ontology_store", None)
    llm = getattr(runtime, "llm_provider", None)
    compile_model = str(
        getattr(llm, "model_id", None)
        or getattr(llm, "model", None)
        or getattr(llm, "_model", None)
        or ""
    )
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

    embedding_provider = getattr(runtime, "embedding_provider", None)
    if embedding_provider is not None:
        try:
            from job_ftch.application.profile_inputs import embed_profile_examples

            managed = await embed_profile_examples(managed, embedding_provider)
        except Exception as exc:  # noqa: BLE001
            sync_errors.append(f"embed:{type(exc).__name__}: {exc}")

    await runner.save_and_activate_candidate_profile(tenant_id, managed)
    pos_added = 0
    if ontology_store is not None:
        try:
            compiled = await compile_profile_ontology(
                managed, llm=llm, ontology_store=ontology_store
            )
            compile_model = str(compiled.get("model") or compile_model)
            ontology_errors.extend(str(err) for err in compiled.get("ontology_errors") or [])
            pos_added = int(compiled.get("pos_added") or 0)
            if pos_added == 0:
                skills = await ontology_store.list_skills()
                roles = await ontology_store.list_roles()
                pos_added = len(skills) + len(roles)
        except Exception as exc:  # noqa: BLE001
            ontology_errors.append(f"{type(exc).__name__}: {exc}")
            try:
                skills = await ontology_store.list_skills()
                roles = await ontology_store.list_roles()
                pos_added = len(skills) + len(roles)
            except Exception as list_exc:  # noqa: BLE001
                ontology_errors.append(f"{type(list_exc).__name__}: {list_exc}")
    from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

    mark_prefilter_dirty(getattr(runtime, "settings", None))

    # Full profile mirror (same as bot document path) for scorer visibility.
    try:
        from job_ftch.application.shot_sync import sync_profile_to_shot_store

        await sync_profile_to_shot_store(
            profile=managed,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        sync_errors.append(f"sync_profile:{type(exc).__name__}: {exc}")

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
        "ontology_errors": ontology_errors,
        "pos_added": pos_added,
        "model": compile_model,
        "ontology_store": runtime.ontology_store is not None,
        "embedding_provider": embedding_provider is not None,
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
    from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

    mark_prefilter_dirty(getattr(runner.get_runtime(tenant_id), "settings", None))
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


def example_error(code: str, message: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": code, "message": message}
    payload.update(extra)
    return payload


def _shot_kind(kind: str) -> ShotKind:
    return "job" if kind == "vacancy" else "resume"


def _public_counts(counts: dict[str, int]) -> dict[str, int]:
    return {_PUBLIC_EXAMPLE_KEYS.get(key, key): value for key, value in counts.items()}


def _bucket_keys(*, kind: str, label: str | None) -> list[str]:
    keys: list[str]
    if kind == "resume":
        keys = ["positive_resume", "negative_resume"]
    elif kind == "vacancy":
        keys = ["positive_job", "negative_job"]
    else:
        keys = ["positive_resume", "negative_resume", "positive_job", "negative_job"]
    if label == "positive":
        return [key for key in keys if key.startswith("positive_")]
    if label == "negative":
        return [key for key in keys if key.startswith("negative_")]
    return keys


def _key_to_kind_label(key: str) -> tuple[ExampleKind, ExampleLabel]:
    label: ExampleLabel = "negative" if key.startswith("negative_") else "positive"
    kind: ExampleKind = "vacancy" if key.endswith("job") else "resume"
    return kind, label


async def get_examples_summary(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    listed = await list_shots(runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id)
    counts = _public_counts(listed["counts"])
    return {
        "tenant_id": listed["tenant_id"],
        "user_id": listed["user_id"],
        "profile_id": listed["profile_id"],
        "counts": counts,
        "total": sum(counts.values()),
    }


async def list_operator_examples(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    profile_id: str | None = None,
    kind: str = "all",
    label: str | None = None,
) -> dict[str, Any]:
    if kind not in _EXAMPLE_CLEAR_KINDS:
        return example_error(
            "invalid_arguments",
            "kind must be one of all|resume|vacancy",
            kind=kind,
        )
    if label is not None and label not in _EXAMPLE_LABELS:
        return example_error(
            "invalid_arguments",
            "label must be one of positive|negative",
            label=label,
        )
    listed = await list_shots(runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id)
    wanted = set(_bucket_keys(kind=kind, label=label))
    examples = {
        _PUBLIC_EXAMPLE_KEYS.get(key, key): list(values)
        for key, values in listed["examples"].items()
        if key in wanted
    }
    return {
        "tenant_id": listed["tenant_id"],
        "user_id": listed["user_id"],
        "profile_id": listed["profile_id"],
        "kind": kind,
        "label": label,
        "examples": examples,
        "counts": {key: len(values) for key, values in examples.items()},
    }


async def add_operator_example(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    kind: str,
    label: str,
    text: str = "",
    texts: list[str] | None = None,
    profile_id: str | None = None,
    refresh_policy: str = "auto",
) -> dict[str, Any]:
    if kind not in _EXAMPLE_KINDS:
        return example_error(
            "invalid_arguments",
            "kind must be one of resume|vacancy",
            kind=kind,
        )
    if label not in _EXAMPLE_LABELS:
        return example_error(
            "invalid_arguments",
            "label must be one of positive|negative",
            label=label,
        )
    if refresh_policy not in _REFRESH_POLICIES:
        return example_error(
            "invalid_arguments",
            "refresh_policy must be one of auto|defer|sync",
            refresh_policy=refresh_policy,
        )
    items: list[str] = []
    if text and text.strip():
        items.append(_strip_shell_noise(text))
    if texts:
        items.extend(_strip_shell_noise(item) for item in texts if item and item.strip())
    items = [item for item in items if item]
    if not items:
        return example_error("invalid_arguments", "text or texts[] must be non-empty")

    polarity = cast("ShotPolarity", label)
    shot_kind = _shot_kind(kind)
    if refresh_policy == "defer":
        managed = await ensure_managed_profile(
            runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
        )
        for cleaned in items:
            managed = add_example_to_profile(managed, cleaned, kind=example_kind(polarity, shot_kind))
        await runner.save_and_activate_candidate_profile(tenant_id, managed)
        from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

        mark_prefilter_dirty(getattr(runner.get_runtime(tenant_id), "settings", None))
        examples = list_examples(managed)
        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "profile_id": managed.profile_id,
            "kind": kind,
            "label": label,
            "added": len(items),
            "counts": _public_counts({key: len(values) for key, values in examples.items()}),
            "refresh_policy": refresh_policy,
            "refresh_deferred": True,
            "prefilter_dirty": True,
            "shot_sync_errors": [],
            "ontology_errors": [],
        }

    result = await add_shots(
        runner,
        tenant_id=tenant_id,
        user_id=user_id,
        polarity=polarity,
        kind=shot_kind,
        texts=items,
        profile_id=profile_id,
    )
    return {
        "tenant_id": result["tenant_id"],
        "user_id": result["user_id"],
        "profile_id": result["profile_id"],
        "kind": kind,
        "label": label,
        "added": result["added"],
        "counts": _public_counts(result["counts"]),
        "refresh_policy": refresh_policy,
        "refresh_deferred": False,
        "prefilter_dirty": True,
        "shot_sync_errors": result["shot_sync_errors"],
        "ontology_errors": result["ontology_errors"],
        "pos_added": result.get("pos_added", 0),
        "ontology_store": result["ontology_store"],
        "embedding_provider": result["embedding_provider"],
    }


def public_job_group(group: Any) -> dict[str, Any]:
    """Operator-readable JobGroup dump with a top-level title."""
    payload = group.model_dump(mode="json") if hasattr(group, "model_dump") else dict(group)
    job = getattr(group, "canonical_job", None)
    title = None
    company = None
    if job is not None:
        title = (
            getattr(job, "title", None)
            or getattr(job, "title_normalized", None)
            or getattr(job, "title_raw", None)
        )
        company = getattr(job, "company", None) or getattr(job, "company_canonical", None)
    nested = payload.get("canonical_job") if isinstance(payload.get("canonical_job"), dict) else {}
    if not title:
        title = nested.get("title") or nested.get("title_normalized") or nested.get("title_raw")
    if not company:
        company = nested.get("company") or nested.get("company_canonical")
    payload["title"] = title or ""
    payload["company"] = company
    return payload


async def remove_operator_example(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    kind: str,
    label: str,
    index: int,
    profile_id: str | None = None,
) -> dict[str, Any]:
    if kind not in _EXAMPLE_KINDS:
        return example_error(
            "invalid_arguments",
            "kind must be one of resume|vacancy",
            kind=kind,
        )
    if label not in _EXAMPLE_LABELS:
        return example_error(
            "invalid_arguments",
            "label must be one of positive|negative",
            label=label,
        )
    try:
        result = await remove_shot(
            runner,
            tenant_id=tenant_id,
            user_id=user_id,
            polarity=cast("ShotPolarity", label),
            kind=_shot_kind(kind),
            index=index,
            profile_id=profile_id,
        )
    except ValueError as exc:
        return example_error("invalid_arguments", str(exc), kind=kind, label=label, index=index)
    return {
        "tenant_id": result["tenant_id"],
        "user_id": result["user_id"],
        "profile_id": result["profile_id"],
        "kind": kind,
        "label": label,
        "removed_index": result["removed_index"],
        "removed_preview": result["removed_preview"],
        "counts": _public_counts(result["counts"]),
        "prefilter_dirty": True,
        "shot_sync_error": result["shot_sync_error"],
    }


async def clear_operator_examples(
    runner: Any,
    *,
    tenant_id: str,
    user_id: str,
    kind: str = "all",
    profile_id: str | None = None,
) -> dict[str, Any]:
    if kind not in _EXAMPLE_CLEAR_KINDS:
        return example_error(
            "invalid_arguments",
            "kind must be one of all|resume|vacancy",
            kind=kind,
        )
    managed = await ensure_managed_profile(
        runner, tenant_id=tenant_id, user_id=user_id, profile_id=profile_id
    )
    examples = list_examples(managed)
    removed = 0
    sync_errors: list[str] = []
    for key in _bucket_keys(kind=kind, label=None):
        example_kind_name, label = _key_to_kind_label(key)
        role = shot_role(
            user_id=user_id,
            tenant_id=tenant_id,
            kind=_shot_kind(example_kind_name),
            polarity=label,
        )
        texts = list(examples.get(key, []))
        ekind = example_kind(label, _shot_kind(example_kind_name))
        for text in texts:
            try:
                await remove_shot_async(text=text, role=role)
            except Exception as exc:  # noqa: BLE001 - profile still cleared
                sync_errors.append(f"{type(exc).__name__}: {exc}")
            managed = remove_example_from_profile(managed, ekind, 0)
            removed += 1
    await runner.save_and_activate_candidate_profile(tenant_id, managed)
    from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

    mark_prefilter_dirty(getattr(runner.get_runtime(tenant_id), "settings", None))
    remaining = list_examples(managed)
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "profile_id": managed.profile_id,
        "kind": kind,
        "removed": removed,
        "counts": _public_counts({key: len(values) for key, values in remaining.items()}),
        "prefilter_dirty": True,
        "shot_sync_errors": sync_errors,
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
