from __future__ import annotations

from job_ftch.sinks.review_artifact import compact_review_payload


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
