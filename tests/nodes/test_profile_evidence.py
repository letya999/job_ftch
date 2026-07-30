import numpy as np
import pytest

from job_ftch.domain.profile import ProfileCatalog, SearchProfile
from job_ftch.nodes.lexical_evidence import LexicalEvidenceNode
from job_ftch.nodes.profile_semantic_evidence import ProfileSemanticEvidenceNode


@pytest.mark.asyncio
async def test_profile_semantic_evidence_uses_existing_job_vector(make_job_record) -> None:
    item = make_job_record(metadata={"bgem3_dense": [1.0, 0.0]})
    node = ProfileSemanticEvidenceNode(
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        np.asarray([[0.0, 1.0]], dtype=np.float32),
    )

    result = await node.process(item)

    assert result.metadata["profile_semantic_positive"] == 1.0
    assert result.metadata["profile_semantic_negative"] == 0.0
    assert result.metadata["profile_semantic_margin"] == 1.0


@pytest.mark.asyncio
async def test_lexical_evidence_is_observational(make_job_record) -> None:
    node = LexicalEvidenceNode(
        SearchProfile(target_roles=("ML Engineer",), anti_preferences=("sales",))
    )
    item = make_job_record(title="ML Engineer", description="Not a sales role")

    result = await node.process(item)

    assert result.metadata["lexical_positive_matches"] == ("ml engineer",)
    assert result.metadata["lexical_negative_matches"] == ("sales",)


@pytest.mark.asyncio
async def test_lexical_evidence_records_matches_per_profile(make_job_record) -> None:
    catalog = ProfileCatalog(
        catalog_name="test",
        profiles=(
            SearchProfile(profile_id="first", anti_preferences=("mlops",)),
            SearchProfile(profile_id="second", anti_preferences=("computer vision",)),
        ),
    )

    result = await LexicalEvidenceNode(catalog).process(
        make_job_record(description="Build and operate MLOps pipelines")
    )

    assert result.metadata["lexical_profile_matches"] == {
        "first": {"positive": (), "negative": ("mlops",)},
        "second": {"positive": (), "negative": ()},
    }
