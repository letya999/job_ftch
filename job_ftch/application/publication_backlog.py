"""Accepted identities survive publication windows and process restarts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord


CANDIDATES_KEY = "bot_publish:candidates"


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
        await add(CANDIDATES_KEY, identity)
        return not existed
    return False


async def mark_publication_handled(
    store: object, target: str, job: object, *, reason: str = "delivered"
) -> None:
    add = getattr(store, "set_add", None)
    set_state = getattr(store, "set_run_state", None)
    identity = candidate_id(job)
    if callable(add) and callable(set_state) and identity:
        await set_state(
            f"{handled_key(target)}:outcome:{identity}",
            json.dumps({"reason": reason, "at": datetime.now(UTC).isoformat()}),
        )
        await add(handled_key(target), identity)


async def pending_candidate_ids(store: object, target: str) -> frozenset[str]:
    members = getattr(store, "set_members", None)
    if not callable(members):
        return frozenset()
    return frozenset(await members(CANDIDATES_KEY) - await members(handled_key(target)))
