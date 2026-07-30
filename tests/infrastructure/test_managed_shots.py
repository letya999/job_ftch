from unittest.mock import MagicMock, patch

import pytest

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.infrastructure.relevance.managed_shots import RegistryManagedShotBackend


@pytest.fixture
def backend():
    settings = MagicMock()
    return RegistryManagedShotBackend(settings)


@pytest.fixture
def profile():
    return ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="User"),
            search_profiles=(
                SearchProfile(
                    positive_example_texts=("pos1",),
                    negative_example_texts=("neg1",),
                    positive_job_example_texts=("pos_job1",),
                    negative_job_example_texts=("neg_job1",),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_sync_profile_no_store_no_provider_returns_zero(backend, profile):
    with (
        patch(
            "job_ftch.infrastructure.relevance.managed_shots.shot_registry.get_store",
            return_value=None,
        ),
        patch(
            "job_ftch.infrastructure.relevance.managed_shots._try_get_qdrant_provider",
            return_value=None,
        ),
    ):
        result = await backend.sync_profile_to_shot_store(
            profile=profile, tenant_id="t1", user_id="u1"
        )
        assert result == (0, 0)


@pytest.mark.asyncio
async def test_sync_profile_store_none_provider_present_warns_not_crashes(backend, profile):
    provider_mock = MagicMock()
    provider_mock.encode.return_value = {"dense": [0.1, 0.2], "sparse": {"1": 0.5}}

    with (
        patch(
            "job_ftch.infrastructure.relevance.managed_shots.shot_registry.get_store",
            return_value=None,
        ),
        patch(
            "job_ftch.infrastructure.relevance.managed_shots._try_get_qdrant_provider",
            return_value=provider_mock,
        ),
        patch(
            "job_ftch.infrastructure.relevance.managed_shots._try_get_qdrant_store",
            return_value=None,
        ),
        patch("job_ftch.infrastructure.relevance.managed_shots.logger") as logger_mock,
    ):
        result = await backend.sync_profile_to_shot_store(
            profile=profile, tenant_id="t1", user_id="u1"
        )
        assert result == (0, 0)
        logger_mock.warning.assert_any_call(
            "shot_store_unavailable: store is None, shot not synced to in-memory backend"
        )


def test_add_shot_qdrant_error_logs_warning(backend):
    qdrant_mock = MagicMock()
    qdrant_mock.upsert_shots_with_ids.side_effect = Exception("qdrant error")

    with (
        patch(
            "job_ftch.infrastructure.relevance.managed_shots.shot_registry.get_store",
            return_value=None,
        ),
        patch(
            "job_ftch.infrastructure.relevance.managed_shots._try_get_qdrant_store",
            return_value=qdrant_mock,
        ),
        patch("job_ftch.infrastructure.relevance.managed_shots.logger") as logger_mock,
    ):
        backend.add_shot(
            text="some text", label="positive", role="test_role", tenant_id="t1", user_id="u1"
        )

        assert logger_mock.warning.call_count > 0
        call_args = logger_mock.warning.call_args[0]
        assert "shot_add_failed store=MagicMock: qdrant error" in call_args[0] % call_args[1:]
