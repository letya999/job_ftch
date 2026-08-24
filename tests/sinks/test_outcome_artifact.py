from __future__ import annotations

from job_ftch.domain.rejected import RejectedItem, RejectedOutcome
from job_ftch.sinks.outcome_artifact import compact_rejected_payload, compact_review_payload


def test_compact_review_payload_keeps_decision_context_without_repeated_state(
    make_job_record,
) -> None:
    job = make_job_record().model_copy(
        update={
            "description_raw": "x" * 7_000,
            "metadata": {
                "source_run_id": "run-1",
                "decision_reasons": ["profile_relevance_uncertain"],
                "_llm_relevance": {"decision": "reject"},
                "ontology_snapshots": {"default": {"payload_json": "large"}},
                "original_posting_text": "duplicate text",
            },
        }
    )

    payload = compact_review_payload(job)

    assert payload["source_run_id"] == "run-1"
    assert payload["decision_reasons"] == ["profile_relevance_uncertain"]
    assert payload["llm_relevance"] == {"decision": "reject"}
    assert len(payload["description_excerpt"]) == 6_000
    assert payload["description_truncated"] is True
    assert "metadata" not in payload
    assert "ontology_snapshots" not in payload
    assert payload["lane"] == "review"


def test_compact_rejected_payload_drops_full_snapshot(make_job_record) -> None:
    job = make_job_record().model_copy(
        update={
            "title": "ML Engineer",
            "description_raw": "y" * 8_000,
            "metadata": {"source_run_id": "run-9", "ontology_snapshots": {"a": 1}},
        }
    )
    rejected = RejectedItem(
        outcome=RejectedOutcome.DROPPED,
        reason="policy_reject",
        details="DecisionNode selected REJECT",
        stage="DecisionNode",
        item_type="JobRecord",
        source_kind=job.source_kind,
        source_name=job.source_name,
        stable_id=job.stable_id,
        raw_item_id=job.raw_item_id,
        trace={"source_run_id": "run-9", "noise": "drop-me"},
        snapshot=job.model_dump(mode="json"),
    )

    payload = compact_rejected_payload(rejected)

    assert payload["lane"] == "rejected"
    assert payload["reason"] == "policy_reject"
    assert payload["source_run_id"] == "run-9"
    assert payload["title"] == "ML Engineer"
    assert "snapshot" not in payload
    assert "noise" not in payload["trace"]
    assert len(payload["description_excerpt"]) == 4_000
    assert payload["description_truncated"] is True
