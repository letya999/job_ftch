from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.application.profile_inputs import embed_profile_examples
from job_ftch.domain import ManagedCandidateProfile, SearchProfile
from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile


class _DeterministicEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), float(index + 2)] for index, _ in enumerate(texts)]


def _managed_profile(
    *,
    positive_example_texts: tuple[str, ...] = (),
    negative_example_texts: tuple[str, ...] = (),
    positive_job_example_texts: tuple[str, ...] = (),
    negative_job_example_texts: tuple[str, ...] = (),
    embedding_vector: tuple[float, ...] | None = (9.0, 9.0),
    negative_embedding_vectors: tuple[tuple[float, ...], ...] = ((8.0, 8.0),),
) -> ManagedCandidateProfile:
    return ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        updated_at=datetime.now(UTC) - timedelta(days=1),
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="Test"),
            search_profiles=(
                SearchProfile(
                    profile_id="p1",
                    positive_example_texts=positive_example_texts,
                    negative_example_texts=negative_example_texts,
                    positive_job_example_texts=positive_job_example_texts,
                    negative_job_example_texts=negative_job_example_texts,
                    embedding_vector=embedding_vector,
                    negative_embedding_vectors=negative_embedding_vectors,
                ),
            ),
        ),
    )


@pytest.mark.anyio
async def test_embed_profile_examples_clears_negative_vectors_when_last_negative_deleted() -> None:
    profile = _managed_profile(positive_example_texts=("resume",))

    result = await embed_profile_examples(profile, _DeterministicEmbedProvider())

    sp = result.profile.search_profiles[0]
    assert sp.embedding_vector is not None
    assert sp.negative_embedding_vectors == ()


@pytest.mark.anyio
async def test_embed_profile_examples_clears_positive_vector_when_last_positive_deleted() -> None:
    profile = _managed_profile(negative_example_texts=("reject",))

    result = await embed_profile_examples(profile, _DeterministicEmbedProvider())

    sp = result.profile.search_profiles[0]
    assert sp.embedding_vector is None
    assert sp.negative_embedding_vectors != ()


@pytest.mark.anyio
async def test_embed_profile_examples_clears_all_vectors_when_all_examples_deleted() -> None:
    profile = _managed_profile()

    result = await embed_profile_examples(profile, _DeterministicEmbedProvider())

    sp = result.profile.search_profiles[0]
    assert sp.embedding_vector is None
    assert sp.negative_embedding_vectors == ()
    assert result.updated_at > profile.updated_at
