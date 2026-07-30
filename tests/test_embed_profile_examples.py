import pytest


@pytest.mark.anyio
async def test_embed_profile_examples_uses_batch_embed():
    """embed_profile_examples must call embed(list) not embed_query — regression for OpenAI provider."""
    from job_ftch.application.profile_inputs import embed_profile_examples
    from job_ftch.domain import ManagedCandidateProfile, SearchProfile
    from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile

    calls = []

    class _BatchEmbedProvider:
        """Minimal provider implementing only embed(list[str]) — like OpenAIEmbeddingProvider."""

        async def embed(self, texts: list[str]) -> list[list[float]]:
            calls.append(texts)
            return [[0.1] * 8 for _ in texts]

        # NO embed_query, NO embed_passage — intentional

    profile = ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="T"),
            search_profiles=(
                SearchProfile(
                    profile_id="p1",
                    positive_example_texts=("first example", "second example"),
                    negative_example_texts=("bad example",),
                ),
            ),
        ),
    )

    result = await embed_profile_examples(profile, _BatchEmbedProvider())

    # embed() must have been called (batch), not embed_query per-item
    assert len(calls) >= 1, "embed() must be called at least once"
    # embedding_vector must be set
    sp = result.profile.search_profiles[0]
    assert sp.embedding_vector is not None
    assert len(sp.embedding_vector) == 8


@pytest.mark.anyio
async def test_embed_profile_examples_combines_resume_and_job_shots():
    """embed_profile_examples must combine resume + vacancy shots into a single embedding batch."""
    from job_ftch.application.profile_inputs import embed_profile_examples
    from job_ftch.domain import ManagedCandidateProfile, SearchProfile
    from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile

    call_texts = []

    class _BatchEmbedProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            call_texts.extend(texts)
            return [[0.1] * 8 for _ in texts]

    profile = ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="T"),
            search_profiles=(
                SearchProfile(
                    profile_id="p1",
                    positive_example_texts=("resume pos A", "resume pos B"),
                    negative_example_texts=("resume neg X",),
                    positive_job_example_texts=("job pos C",),
                    negative_job_example_texts=("job neg Y",),
                ),
            ),
        ),
    )

    result = await embed_profile_examples(profile, _BatchEmbedProvider())

    sp = result.profile.search_profiles[0]
    assert sp.embedding_vector is not None

    all_pos = {"resume pos A", "resume pos B", "job pos C"}
    all_neg = {"resume neg X", "job neg Y"}
    assert set(call_texts) == all_pos | all_neg, (
        f"Expected {all_pos | all_neg}, got {set(call_texts)}"
    )
