from __future__ import annotations

from types import SimpleNamespace

from job_ftch.application.graph.pipeline_stage import GraphPipelineStage
from job_ftch.application.pipeline import Pipeline


async def test_graph_pipeline_stage_preserves_terminal_status(make_raw_item) -> None:
    raw = make_raw_item()

    class Executor:
        async def run_many(self, item):
            assert item is raw
            return [
                SimpleNamespace(
                    item=raw,
                    status="REJECT",
                    node_events={"dedup": {"outcome": "drop", "effect": "gate"}},
                )
            ]

    result = await GraphPipelineStage(Executor()).process(raw)

    assert result[0].status == "REJECT"
    assert result[0].first_loss_node == "dedup"
    assert result[0].has_terminal_decision is False
    assert result[0].terminal_reasons == ()
    assert result[0].node_events == {"dedup": {"outcome": "drop", "effect": "gate"}}


def test_pipeline_graph_result_keeps_terminal_reject_for_rejected_sink(make_job_record) -> None:
    record = make_job_record()
    graph_result = SimpleNamespace(
        source_item=record,
        item=record,
        status="REJECT",
        first_loss_node=None,
        has_terminal_decision=True,
        terminal_reasons=(),
        node_events={},
    )

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._sanitize_node = object()
    child = Pipeline._graph_executor_children(pipeline, (graph_result,), "run-1")[0]

    assert child["outcome"] == "emitted"
    assert child["current"].metadata["source_run_id"] == "run-1"


def test_pipeline_graph_result_marks_terminal_deferred_retryable(make_job_record) -> None:
    record = make_job_record()
    graph_result = SimpleNamespace(
        source_item=record,
        item=record,
        status="DEFERRED",
        first_loss_node=None,
        has_terminal_decision=True,
        terminal_reasons=("relevance_llm_required",),
        node_events={},
    )

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._sanitize_node = object()
    child = Pipeline._graph_executor_children(pipeline, (graph_result,), "run-1")[0]

    assert child["outcome"] == "deferred"
    assert child["current"].metadata["work_state"] == "deferred"
    assert child["current"].metadata["deferred_reason"] == "relevance_llm_required"
    assert child["current"].metadata["decision_reasons"] == ("relevance_llm_required",)


def test_pipeline_graph_result_preserves_node_events_for_drop_artifact(make_job_record) -> None:
    record = make_job_record()
    node_events = {
        "tfidf_logreg_prefilter": {
            "node_id": "tfidf_logreg_prefilter",
            "outcome": "drop",
            "reason": "gate_returned_none",
        }
    }
    graph_result = SimpleNamespace(
        source_item=record,
        item=record,
        status="REJECT",
        first_loss_node="tfidf_logreg_prefilter",
        has_terminal_decision=False,
        terminal_reasons=(),
        node_events=node_events,
    )

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._sanitize_node = object()
    child = Pipeline._graph_executor_children(pipeline, (graph_result,), "run-1")[0]

    assert child["outcome"] == "dropped_node"
    assert child["drop_stage"] == "tfidf_logreg_prefilter"
    assert child["drop_reason"] == "node_returned_none:tfidf_logreg_prefilter"
    assert child["trace"]["node_events"] == node_events


def test_graph_metrics_separate_terminal_statuses_from_execution_outcomes() -> None:
    stage = GraphPipelineStage(SimpleNamespace(graph=SimpleNamespace(graph_hash="graph-1")))

    stage._record_node_metrics(
        {
            "decision": {
                "calls": 1,
                "elapsed_ms": 2.5,
                "outcome": "pass",
                "terminal_status": "ACCEPT",
                "terminal_reasons": ["profile_relevance_confirmed"],
            }
        }
    )

    assert stage.node_metrics["decision"] == {
        "calls": 1,
        "elapsed_ms": 2.5,
        "outcomes": {"pass": 1},
        "terminal_statuses": {"ACCEPT": 1},
        "terminal_reasons": {"profile_relevance_confirmed": 1},
    }


def test_graph_stage_exposes_runtime_node_counters_once_per_instance() -> None:
    node = SimpleNamespace(stats={"llm_relevance_calls": 4, "llm_relevance_cache_hits": 2})
    executor = SimpleNamespace(
        graph=SimpleNamespace(graph_hash="graph-1"),
        factories={"judge": node, "judge_alias": node},
    )

    assert GraphPipelineStage(executor).runtime_stats() == {
        "llm_relevance_calls": 4,
        "llm_relevance_cache_hits": 2,
    }
