from __future__ import annotations

from application.outcomes import PipelineStage, RejectReason
from application.run_summary import RunSummary


def test_run_summary_serializes_datetimes_and_counts() -> None:
    summary = RunSummary(run_id="run-1")
    summary.fetched = 1
    summary.record_stage(PipelineStage.SANITIZE)
    summary.record_reason(RejectReason.EMPTY_TEXT)
    summary.record_source("debug:fixture")
    summary.finish()

    payload = summary.as_dict()

    assert payload["run_id"] == "run-1"
    assert payload["fetched"] == 1
    assert payload["stage_counts"] == {"sanitize": 1}
    assert payload["reason_counts"] == {"empty_text": 1}
    assert payload["source_counts"] == {"debug:fixture": 1}
    assert isinstance(payload["started_at"], str)
    assert isinstance(payload["finished_at"], str)
    assert "datetime.datetime" not in payload["finished_at"]
