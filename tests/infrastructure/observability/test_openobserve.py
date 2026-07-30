from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.application.pipeline import RunSummary
from job_ftch.domain import SourceHealth
from job_ftch.infrastructure.observability.openobserve import (
    _ContextAttributesFilter,
    _counter_mapping,
    _counter_value,
    _runtime_state_snapshots,
    record_runtime_state_metrics,
)


def test_context_filter_promotes_only_safe_correlation_fields() -> None:
    tokens = bind_contextvars(
        source_run_id="run-123",
        tenant_id="ai_jobs",
        graph_hash="graph-456",
        source_id="forproducts",
        ignored_payload={"large": "payload"},
    )
    try:
        record = logging.LogRecord("job_ftch", logging.INFO, __file__, 1, "event", (), None)

        assert _ContextAttributesFilter().filter(record) is True
        assert record.source_run_id == "run-123"  # type: ignore[attr-defined]
        assert record.tenant_id == "ai_jobs"  # type: ignore[attr-defined]
        assert record.graph_hash == "graph-456"  # type: ignore[attr-defined]
        assert record.source_id == "forproducts"  # type: ignore[attr-defined]
        assert not hasattr(record, "ignored_payload")
    finally:
        reset_contextvars(**tokens)


def test_operational_counter_helpers_fail_closed_on_malformed_runtime_stats() -> None:
    assert _counter_value("7") == 7
    assert _counter_value("invalid") == 0
    assert _counter_value({"not": "a count"}) == 0
    assert _counter_mapping({"accept": 7}) == {"accept": 7}
    assert _counter_mapping([("accept", 7)]) == {}


def test_runtime_state_metrics_snapshot_exposes_source_and_publish_state(monkeypatch) -> None:
    from job_ftch.infrastructure.observability import openobserve

    monkeypatch.setattr(openobserve, "_configured", True)
    monkeypatch.setattr(openobserve, "force_flush_openobserve", lambda: None)
    _runtime_state_snapshots.clear()
    now = datetime.now(UTC)
    summary = RunSummary(
        tenant_id="ai_jobs",
        source_run_id="run-123",
        finished_at=now,
        failed=0,
    )
    health = SourceHealth(
        source_id="career_site:example",
        source_kind="career_site",
        source_name="example",
        last_run_at=now.isoformat(),
        last_success_at=(now - timedelta(seconds=30)).isoformat(),
        failure_streak=2,
        success_count=3,
        last_fetched=4,
        last_emitted=1,
        last_failed=0,
        last_quarantined=0,
        baseline_emitted=3.0,
        drift_ratio=0.33,
        degraded=True,
        status="degraded",
    )

    record_runtime_state_metrics(
        summary,
        source_health=[health],
        scheduler_state={
            "bot_scheduler:last_attempt_at": (now - timedelta(seconds=20)).isoformat(),
            "bot_scheduler:last_success_at": (now - timedelta(seconds=10)).isoformat(),
            "bot_scheduler:last_publish_success_at": (now - timedelta(seconds=5)).isoformat(),
            "bot_scheduler:pending_publish_since": "",
            "bot_scheduler:last_publish_error": "rate limit",
            "bot_scheduler:last_publish_sent": "7",
        },
    )

    assert _runtime_state_snapshots["job_ftch.source.health.degraded"][0][0] == 1.0
    assert _runtime_state_snapshots["job_ftch.source.health.failure_streak"][0][0] == 2.0
    assert _runtime_state_snapshots["job_ftch.bot.scheduler.publish_error_present"][0][0] == 1.0
    assert _runtime_state_snapshots["job_ftch.bot.scheduler.last_publish_sent"][0][0] == 7.0
