import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.profile import ProfileCatalog, SearchProfile
from job_ftch.nodes.embedding_prefilter import EmbeddingPrefilterNode


class FakeEmbeddingProvider:
    async def embed_query(self, texts: list[str]) -> list[list[float]]:
        # Map specific words to deterministic vectors for testing
        results = []
        for text in texts:
            t = text.lower()
            if "ml engineer" in t or "ml-инженер" in t:
                results.append([1.0, 0.0, 0.0])
            elif "qa" in t:
                results.append([0.0, 1.0, 0.0])
            else:
                results.append([0.0, 0.0, 1.0])
        return results


@pytest.mark.asyncio
async def test_embedding_prefilter_passes_relevant_russian():
    catalog = ProfileCatalog(
        profiles=(
            SearchProfile(
                profile_id="p1",
                name="AI",
                target_roles=("ML Engineer",),
            ),
        )
    )
    node = EmbeddingPrefilterNode(catalog, FakeEmbeddingProvider())

    # "ищем ML-инженера" should map to [1,0,0], same as "ML Engineer"
    item = RawItem(
        text="ищем ML-инженера в стартап",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="123",
    )

    processed = await node.process(item)
    assert processed is not None
    assert processed.metadata["embedding_role_match"] == 1.0
    assert processed.metadata["embedding_prefilter_decision"] == "pass"


@pytest.mark.asyncio
async def test_embedding_prefilter_low_signal_passes_through_off_target():
    catalog = ProfileCatalog(
        profiles=(
            SearchProfile(
                profile_id="p1",
                name="AI",
                target_roles=("ML Engineer",),
                relevance_threshold=0.4,
            ),
        )
    )
    node = EmbeddingPrefilterNode(catalog, FakeEmbeddingProvider(), drop_threshold=0.5)

    # QA maps to [0,1,0]; cosine sim with ML Engineer [1,0,0] is 0.0 -> below drop_threshold.
    # The node no longer drops; the downstream SemanticPrefilterNode owns the drop decision.
    item = RawItem(
        text="ищем QA инженера",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="123",
        metadata={"semantic_prefilter_best_score": 0.1},
    )

    processed = await node.process(item)
    assert processed is not None
    assert processed.metadata["embedding_role_match"] == 0.0
    assert processed.metadata["embedding_prefilter_decision"] == "low_signal"


@pytest.mark.asyncio
async def test_embedding_prefilter_uncertain_zone():
    catalog = ProfileCatalog(
        profiles=(
            SearchProfile(
                profile_id="p1",
                name="AI",
                target_roles=("ML Engineer",),
            ),
        )
    )
    # pass=0.5, drop=0.35. We'll simulate 0.4

    class MixedEmbeddingProvider:
        async def embed_query(self, texts: list[str]) -> list[list[float]]:
            # Return [1,0] for the role, and [0.4, 0.9165] for the item
            if "ML Engineer" in texts[0]:
                return [[1.0, 0.0]]
            return [[0.4, 0.9165]]  # cos_sim with [1,0] is 0.4

    node = EmbeddingPrefilterNode(
        catalog, MixedEmbeddingProvider(), pass_threshold=0.5, drop_threshold=0.35
    )

    item = RawItem(
        text="something else",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="123",
    )

    processed = await node.process(item)
    assert processed is not None
    assert processed.metadata["embedding_role_match"] == 0.4
    assert processed.metadata["embedding_prefilter_decision"] == "uncertain"


@pytest.mark.asyncio
async def test_embedding_prefilter_no_op_no_roles():
    catalog = ProfileCatalog(profiles=(SearchProfile(profile_id="p1", target_roles=()),))
    node = EmbeddingPrefilterNode(catalog, FakeEmbeddingProvider())

    item = RawItem(
        text="text", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123"
    )
    processed = await node.process(item)
    assert processed == item
    assert "embedding_role_match" not in processed.metadata
