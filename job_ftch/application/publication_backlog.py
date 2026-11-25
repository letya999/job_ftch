"""Accepted identities survive publication windows and process restarts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


CANDIDATES_KEY = "bot_publish:candidates"
_REVISION_KEY = f"{CANDIDATES_KEY}:revision"
_AUDIENCE_FIELDS = (
    "company",
    "company_canonical",
    "city",
    "country",
    "location",
    "region",
    "work_mode",
    "remote_restrictions",
    "language",
    "languages_detected",
    "description",
    "description_raw",
    "source_kind",
)


def audience_revision(job: object) -> str:
    metadata = getattr(job, "metadata", {}) or {}
    values = [getattr(job, field, None) for field in _AUDIENCE_FIELDS]
    values.extend(
        metadata.get(key)
        for key in ("original_posting_text", "detected_language", "publication_locations")
    )
    return sha256(json.dumps(values, ensure_ascii=False, default=str).encode()).hexdigest()


async def candidate_revision(store: object, identity: str) -> str | None:
    read = getattr(store, "get_run_state", None)
    return await read(f"{_REVISION_KEY}:{identity}") if callable(read) else None


def candidate_id(job: object) -> str | None:
    return getattr(job, "job_id", None) or getattr(job, "stable_id", None)


def handled_key(target: str) -> str:
    return f"bot_publish:handled:{sha256(target.encode()).hexdigest()}"


async def record_publish_candidate(store: object, job: JobRecord) -> bool:
    add = getattr(store, "set_add", None)
    contains = getattr(store, "set_contains", None)
    identity = candidate_id(job)
    if callable(add) and identity:
        existed = await contains(CANDIDATES_KEY, identity) if callable(contains) else False
        write = getattr(store, "set_run_state", None)
        if callable(write):
            revision = audience_revision(job)
            previous = await candidate_revision(store, identity) if existed else None
            if previous != revision:
                if existed:
                    await add(f"{_REVISION_KEY}:changed", identity)
                await write(f"{_REVISION_KEY}:{identity}", revision)
        await add(CANDIDATES_KEY, identity)
        return not existed
    return False


async def mark_publication_handled(
    store: object,
    target: str,
    job: object,
    *,
    reason: str = "delivered",
    revision: str | None = None,
) -> None:
    add = getattr(store, "set_add", None)
    set_state = getattr(store, "set_run_state", None)
    identity = candidate_id(job)
    if callable(add) and callable(set_state) and identity:
        await set_state(
            f"{handled_key(target)}:outcome:{identity}",
            json.dumps(
                {"reason": reason, "at": datetime.now(UTC).isoformat(), "revision": revision}
            ),
        )
        await add(handled_key(target), identity)


def audience_target(target: str, version: str) -> str:
    return f"audience:{version}:{target}"


async def pending_candidate_ids(
    store: object, target: str, *, policy_version: str | None = None
) -> frozenset[str]:
    members = getattr(store, "set_members", None)
    if not callable(members):
        return frozenset()
    pending = await members(CANDIDATES_KEY) - await members(handled_key(target))
    if policy_version:
        bucket = handled_key(audience_target(target, policy_version))
        held = pending & await members(bucket)
        pending = pending - held
        changed = held & await members(f"{_REVISION_KEY}:changed")
        read = getattr(store, "get_run_state", None)
        if callable(read):
            for identity in changed:
                outcome = json.loads(await read(f"{bucket}:outcome:{identity}") or "{}")
                if outcome.get("revision") != await candidate_revision(store, identity):
                    pending = pending | {identity}
    return frozenset(pending)
