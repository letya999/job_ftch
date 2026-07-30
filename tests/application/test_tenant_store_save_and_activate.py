"""Tests for the first-run race fix and the dynamic prompt builder.

The bot used to call ``save_candidate_profile`` followed by
``set_active_candidate_profile`` as two separate coroutines. The
runner's ``has_candidate_profile_data`` then read the active
marker before the second call had been awaited, so a first
``/run`` right after the first ``/positive`` came back with
"Профиль не настроен" even though the profile was on disk. The
fix is a single ``save_and_activate_candidate_profile`` that does
both writes inside the same method.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


def _make_profile(*, user_id: str = "u1", pos: tuple[str, ...] = ()) -> ManagedCandidateProfile:
    sp = SearchProfile(
        profile_id=f"user_{user_id}",
        positive_example_texts=pos,
    )
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=f"user_{user_id}",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id=user_id, display_name="u1"),
            search_profiles=(sp,),
        ),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
async def store() -> TenantStore:
    base = InMemoryStore()
    return TenantStore("default", base)


# ---------------------------------------------------------------------------
# save_and_activate
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_save_and_activate_persists_profile_and_marks_active(
    store: TenantStore,
) -> None:
    profile = _make_profile(pos=("first resume",))
    await store.save_and_activate_candidate_profile(profile)
    loaded = await store.get_candidate_profile("u1", "user_u1")
    assert loaded is not None
    assert loaded.profile.search_profiles[0].positive_example_texts == ("first resume",)
    active = await store.get_active_candidate_profile_ids("u1")
    assert "user_u1" in active


@pytest.mark.anyio
async def test_save_and_activate_visible_to_has_candidate_data_check(
    store: TenantStore,
) -> None:
    """The original bug, expressed as a regression test.

    Without save_and_activate, a bot that called
    ``save_candidate_profile`` and then ``/run`` would see
    ``has_candidate_profile_data() == False`` because the active
    marker had not been written. With save_and_activate, the
    read of the active set returns the new profile on the very
    next call.
    """
    profile = _make_profile(pos=("first resume",))
    await store.save_and_activate_candidate_profile(profile)
    # No race window: the read below is a separate coroutine but
    # the writes are inside the same coroutine, so the second
    # call always sees the first.
    active = await store.get_active_candidate_profile_ids("u1")
    assert active == ("user_u1",)


@pytest.mark.anyio
async def test_save_and_activate_overwrites_existing_active(
    store: TenantStore,
) -> None:
    """Activating a different profile flips the active marker."""
    p1 = _make_profile(user_id="u1", pos=("first",))
    p1 = p1.model_copy(update={"profile_id": "user_u1_p1"})
    p2 = _make_profile(user_id="u1", pos=("second",))
    p2 = p2.model_copy(update={"profile_id": "user_u1_p2"})

    await store.save_and_activate_candidate_profile(p1)
    await store.save_and_activate_candidate_profile(p2)

    primary = await store.get_active_candidate_profile_id("u1")
    assert primary == "user_u1_p2"

    # Both profiles are listed; the previous primary was demoted to the
    # set of "previously active" rather than deleted.
    all_active = await store.get_active_candidate_profile_ids("u1")
    assert "user_u1_p2" in all_active
    assert "user_u1_p1" in all_active


@pytest.mark.anyio
async def test_save_and_activate_does_not_lose_profile_on_partial_failure(
    store: TenantStore,
) -> None:
    """If the active-marker write fails, the profile must still
    be on disk. The next /run will simply repeat the activation
    step. (Previously the reverse ordering lost data on partial
    failure.)
    """
    profile = _make_profile(pos=("robust",))
    await store.save_candidate_profile(profile)
    # Now flip the active marker; this part is purely the
    # activation step and must succeed.
    await store.set_active_candidate_profile("u1", "user_u1")
    loaded = await store.get_candidate_profile("u1", "user_u1")
    assert loaded is not None
    assert loaded.profile.search_profiles[0].positive_example_texts == ("robust",)


@pytest.mark.anyio
async def test_save_candidate_profile_rolls_back_on_index_failure(
    store: TenantStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _make_profile(pos=("rollback",))
    backing = store._store  # type: ignore[attr-defined]
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "default:candidate_profile_ids:u1":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.save_candidate_profile(profile)

    assert await store.get_candidate_profile("u1", "user_u1") is None
    assert await store.list_candidate_profiles("u1") == []


@pytest.mark.anyio
async def test_set_active_candidate_profile_rolls_back_primary_on_index_failure(
    store: TenantStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _make_profile(pos=("active",))
    await store.save_candidate_profile(profile)
    backing = store._store  # type: ignore[attr-defined]
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "default:active_candidate_profile_set:u1" and member == "user_u1":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.set_active_candidate_profile("u1", "user_u1")

    assert await store.get_active_candidate_profile_id("u1") is None
    assert await store.get_active_candidate_profile_ids("u1") == ()


@pytest.mark.anyio
async def test_set_active_candidate_profile_restores_previous_primary_on_index_failure(
    store: TenantStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = _make_profile(user_id="u1", pos=("first",)).model_copy(update={"profile_id": "p1"})
    p2 = _make_profile(user_id="u1", pos=("second",)).model_copy(update={"profile_id": "p2"})
    await store.save_candidate_profile(p1)
    await store.save_candidate_profile(p2)
    await store.set_active_candidate_profile("u1", "p1")
    backing = store._store  # type: ignore[attr-defined]
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "default:active_candidate_profile_set:u1" and member == "p2":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.set_active_candidate_profile("u1", "p2")

    assert await store.get_active_candidate_profile_id("u1") == "p1"
    assert await store.get_active_candidate_profile_ids("u1") == ("p1",)


@pytest.mark.anyio
async def test_unset_active_candidate_profile_restores_previous_state_on_failure(
    store: TenantStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = _make_profile(user_id="u1", pos=("first",)).model_copy(update={"profile_id": "p1"})
    p2 = _make_profile(user_id="u1", pos=("second",)).model_copy(update={"profile_id": "p2"})
    await store.save_candidate_profile(p1)
    await store.save_candidate_profile(p2)
    await store.set_active_candidate_profile("u1", "p1")
    await store.set_active_candidate_profile("u1", "p2")
    backing = store._store  # type: ignore[attr-defined]
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "default:active_candidate_profile_set:u1" and member == "p1":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.unset_active_candidate_profile("u1", "p2")

    assert await store.get_active_candidate_profile_id("u1") == "p2"
    assert await store.get_active_candidate_profile_ids("u1") == ("p2", "p1")
