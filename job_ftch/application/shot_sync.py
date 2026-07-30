"""Application use-case for keeping managed shots scorer-visible and persistent."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from job_ftch.application.contracts import ShotStoreClearError
from job_ftch.application.registry import create_managed_shot_backend
from job_ftch.config import get_settings

__all__ = [
    "ShotStoreClearError",
    "add_shot",
    "add_shot_async",
    "remove_shot",
    "remove_shot_async",
    "remove_user_shots",
    "remove_user_shots_async",
    "sync_profile_to_shot_store",
]

if TYPE_CHECKING:
    from job_ftch.application.contracts import ManagedShotBackend
    from job_ftch.domain import ManagedCandidateProfile


def _backend() -> ManagedShotBackend:
    return cast(
        "ManagedShotBackend",
        create_managed_shot_backend(get_settings()),
    )


def add_shot(
    *,
    text: str,
    label: str,
    role: str,
    tenant_id: str,
    user_id: str,
) -> None:
    """Persist one managed shot and mirror it into scorer-visible state."""
    _backend().add_shot(
        text=text,
        label=label,
        role=role,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def remove_shot(*, text: str, role: str) -> None:
    """Remove one managed shot from scorer-visible and persistent backends."""
    _backend().remove_shot(text=text, role=role)


async def add_shot_async(
    *,
    text: str,
    label: str,
    role: str,
    tenant_id: str,
    user_id: str,
) -> None:
    """Async variant of :func:`add_shot` for callers on an event loop.

    ``add_shot`` runs a synchronous, CPU-bound BGE-M3 encode() call.
    Calling it directly from an async handler (e.g. the Telegram bot)
    blocks the event loop — including Telegram long-polling — for the
    duration of the encode. Offload to a worker thread instead.
    """
    await asyncio.to_thread(
        add_shot,
        text=text,
        label=label,
        role=role,
        tenant_id=tenant_id,
        user_id=user_id,
    )


async def remove_shot_async(*, text: str, role: str) -> None:
    """Async variant of :func:`remove_shot` for callers on an event loop."""
    await asyncio.to_thread(remove_shot, text=text, role=role)


async def remove_user_shots_async(*, tenant_id: str, user_id: str) -> int:
    """Async variant of :func:`remove_user_shots` for callers on an event loop."""
    return await asyncio.to_thread(remove_user_shots, tenant_id=tenant_id, user_id=user_id)


def remove_user_shots(*, tenant_id: str, user_id: str) -> int:
    """Remove all managed shots for a user."""
    return _backend().remove_user_shots(tenant_id=tenant_id, user_id=user_id)


async def sync_profile_to_shot_store(
    *,
    profile: ManagedCandidateProfile,
    tenant_id: str,
    user_id: str,
) -> tuple[int, int]:
    """Rebuild scorer-visible shots from a managed profile."""
    return await _backend().sync_profile_to_shot_store(
        profile=profile,
        tenant_id=tenant_id,
        user_id=user_id,
    )
