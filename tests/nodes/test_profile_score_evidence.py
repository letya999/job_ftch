from __future__ import annotations

import pytest

from job_ftch.domain import JobRecord, MatchDecision, SourceKind
from job_ftch.domain.models import ProfileMatchScore
from job_ftch.nodes.evidence_fanout import ProfileScoreEvidenceProducer


@pytest.mark.asyncio
async def test_profile_feature_evidence_does_not_publish_derived_final_score() -> None:
    record = JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="source",
        title="AI engineer",
        profile_scores=(
            ProfileMatchScore(
                profile_id="p1",
                profile_name="p1",
                final_score=0.9,
                title_score=0.9,
                semantic_role_score=0.8,
                decision=MatchDecision.ACCEPT,
            ),
        ),
    )

    atoms = await ProfileScoreEvidenceProducer().produce(record)

    assert all("final_score" not in atom.evidence_ref for atom in atoms)
    assert all("semantic_role_score" not in atom.evidence_ref for atom in atoms)
