import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.eval.run_pipeline_eval import (
    _close_eval_resources,
    _collect_runtime_node_stats,
    _compute_node_stats,
    _EvalLLMMeter,
    _evaluate_slice_requirements,
    _graph_consumes_bgem3_shots,
    _has_binary_relevance_label,
    _parse_slice_requirement,
    _print_report,
    _select_labeled_rows,
    _source_revision,
    _warm_bgem3_provider,
    _write_invalid_report,
    parse_args,
)


def test_invalid_report_is_atomic_json(tmp_path: Path) -> None:
    path = tmp_path / "eval.invalid.json"

    _write_invalid_report(path, {"status": "INVALID", "completed_parent_items": 3})

    assert path.exists()
    assert (
        path.read_text(encoding="utf-8")
        == '{\n  "status": "INVALID",\n  "completed_parent_items": 3\n}'
    )
    assert not path.with_suffix(".json.tmp").exists()


def test_invalid_run_cleanup_closes_sync_and_async_resources() -> None:
    calls: list[str] = []

    class SyncResource:
        def close(self) -> None:
            calls.append("sync")

    class AsyncResource:
        async def close(self) -> None:
            calls.append("async")

    asyncio.run(_close_eval_resources([SyncResource(), AsyncResource()]))

    assert calls == ["async", "sync"]


def test_node_stats_count_post_accept_skips_separately_from_errors() -> None:
    rows = _compute_node_stats(
        [
            {
                "stage_events": [
                    {
                        "stage": "aggregation",
                        "outcome": "skipped_post_accept",
                        "duration_ms": 0,
                    }
                ]
            }
        ],
        ["aggregation"],
    )

    assert rows == [
        {
            "stage": "aggregation",
            "entered": 1,
            "passed": 0,
            "dropped": 0,
            "skipped": 1,
            "errors": 0,
            "conversion": 0.0,
            "avg_duration_ms": 0.0,
            "total_duration_ms": 0,
        }
    ]


def test_llm_summary_reports_the_model_that_received_calls() -> None:
    class Provider:
        def __init__(self, model: str) -> None:
            self._model = model

        async def classify(self, _prompt: str, _schema: object) -> str:
            return "ok"

    root = _EvalLLMMeter(Provider("gpt-4.1-nano"))
    relevance = _EvalLLMMeter(Provider("gpt-4.1-mini"), parent=root)
    asyncio.run(relevance.classify("classify this", str))

    summary = root.summary()
    assert summary["model"] == "gpt-4.1-mini"
    assert summary["configured_model"] == "gpt-4.1-mini"
    assert summary["root_configured_model"] == "gpt-4.1-nano"
    assert summary["models_used"] == ["gpt-4.1-mini"]


def test_source_revision_falls_back_when_git_is_unavailable(monkeypatch) -> None:
    def fail_git(*_args: object, **_kwargs: object) -> str:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "check_output", fail_git)

    revision = _source_revision()

    assert revision["commit"].startswith("nogit-")
    assert revision["dirty"] is True
    assert revision["source"] == "fallback"
    assert revision["reason"] == "git_unavailable"


def test_llm_summary_uses_provider_reported_usage_and_real_pricing() -> None:
    from job_ftch.application.llm_usage import record_provider_usage

    class Provider:
        _model = "gpt-4.1-mini"

        async def classify(self, _prompt: str, _schema: object) -> str:
            record_provider_usage(
                model=self._model,
                usage=SimpleNamespace(
                    prompt_tokens=1_000,
                    completion_tokens=100,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=200),
                ),
                latency_ms=12,
            )
            return "ok"

    meter = _EvalLLMMeter(Provider())
    asyncio.run(meter.classify("not used for estimation", str))

    summary = meter.summary()
    assert summary["tokens_in"] == 1_000
    assert summary["cached_tokens_in"] == 200
    assert summary["tokens_out"] == 100
    assert summary["cost_usd"] == pytest.approx(0.0005)
    assert summary["cost_is_complete"] is True


def test_binary_label_filter_excludes_unknown_relevance_repairs() -> None:
    assert _has_binary_relevance_label({"relevant": 0})
    assert _has_binary_relevance_label({"relevant": 1})
    assert not _has_binary_relevance_label({"relevant": "unknown"})
    assert not _has_binary_relevance_label({"relevant": None})
    assert not _has_binary_relevance_label({"relevant": True})


def test_exact_selected_ids_keep_order_and_reject_unknown(tmp_path: Path) -> None:
    selected = tmp_path / "ids.txt"
    selected.write_text("b\na\n", encoding="utf-8")
    rows = [
        {"stable_id": "a", "relevant": 1},
        {"stable_id": "b", "relevant": 0},
        {"stable_id": "unknown", "relevant": "unknown"},
    ]

    output = _select_labeled_rows(rows, sample=140, seed=42, full=False, selected_item_ids=selected)

    assert [row["stable_id"] for row in output] == ["b", "a"]


def test_exact_selected_ids_reject_unknown_or_missing_rows(tmp_path: Path) -> None:
    selected = tmp_path / "ids.txt"
    selected.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not binary-labelled"):
        _select_labeled_rows(
            [{"stable_id": "unknown", "relevant": "unknown"}],
            sample=0,
            seed=42,
            full=True,
            selected_item_ids=selected,
        )


def test_llm_only_graph_does_not_consume_bgem3_shots() -> None:
    graph = type(
        "Graph",
        (),
        {
            "spec": type(
                "Spec",
                (),
                {
                    "nodes": [
                        type("Node", (), {"enabled": True, "node": "llm_relevance_evidence"})()
                    ]
                },
            )()
        },
    )()
    assert not _graph_consumes_bgem3_shots(graph)


def test_bgem3_graph_consumes_vector_shots() -> None:
    graph = type(
        "Graph",
        (),
        {
            "spec": type(
                "Spec",
                (),
                {"nodes": [type("Node", (), {"enabled": True, "node": "bgem3_embed"})()]},
            )()
        },
    )()
    assert _graph_consumes_bgem3_shots(graph)


def test_collect_runtime_node_stats_keeps_node_stats_dicts() -> None:
    class _NodeWithStats:
        stats = {"llm_relevance_calls": 3, "llm_relevance_cache_hits": 2}

    class _NodeWithoutStats:
        pass

    assert _collect_runtime_node_stats([_NodeWithStats(), _NodeWithoutStats()]) == {
        "_NodeWithStats": {"llm_relevance_calls": 3, "llm_relevance_cache_hits": 2}
    }


def test_print_report_prefers_typed_relevance_node_stats(capsys) -> None:
    _print_report(
        results=[
            {
                "gold_relevant": 0,
                "exit_stage": "decision",
                "routing_decision": "reject",
            }
        ],
        accept_metrics={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        accept_metrics_adjusted={"recall": 1.0, "f1": 1.0},
        delivered_metrics={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        funnel=[],
        node_stats=[],
        elapsed_s=1.0,
        llm_summary=None,
        runtime_node_stats={
            "LLMRelevanceClassificationNode": {"llm_relevance_calls": 0},
            "LLMRelevanceEvidenceNode": {
                "llm_relevance_calls": 132,
                "llm_relevance_cache_hits": 3,
                "llm_relevance_fallback": 2,
                "llm_relevance_failures": 1,
            },
        },
    )

    output = capsys.readouterr().out
    assert "LLM relevance: calls=132 cache_hits=3 fallback=2 failures=1" in output


def test_pipeline_eval_defaults_to_tenant_profile_source(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_pipeline_eval.py"])
    args = parse_args()
    assert args.profile_source == "tenant"
    assert args.state_mode == "memory"


def test_pipeline_eval_fixture_profile_source_remains_available(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_pipeline_eval.py", "--profile-source", "fixture"])
    args = parse_args()
    assert args.profile_source == "fixture"


def test_pipeline_eval_runtime_state_mode_is_available(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_pipeline_eval.py", "--state-mode", "runtime"])
    args = parse_args()
    assert args.state_mode == "runtime"


def test_pipeline_eval_exposes_bounded_run_arguments(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_pipeline_eval.py",
            "--item-timeout-seconds",
            "12",
            "--run-timeout-seconds",
            "34",
            "--progress-every",
            "2",
            "--invalid-out",
            "artifacts/invalid.json",
        ],
    )

    args = parse_args()

    assert args.item_timeout_seconds == 12
    assert args.run_timeout_seconds == 34
    assert args.progress_every == 2
    assert args.invalid_out == Path("artifacts/invalid.json")


def test_pipeline_eval_production_state_mode_is_explicitly_available(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline_eval.py", "--state-mode", "production", "--runtime", "dev"],
    )
    args = parse_args()
    assert args.state_mode == "production"
    assert args.runtime == "dev"


def test_pipeline_eval_accepts_exact_selected_ids_file(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline_eval.py", "--selected-item-ids", "fixtures/dataset/splits/ids.txt"],
    )
    args = parse_args()
    assert args.selected_item_ids == Path("fixtures/dataset/splits/ids.txt")


def test_pipeline_eval_supports_read_only_selection_validation(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["run_pipeline_eval.py", "--validate-selection-only"])
    args = parse_args()
    assert args.validate_selection_only is True


def test_pipeline_eval_preflight_skips_shots_for_compact_llm_graph() -> None:
    class _Node:
        node = "relevance_judge"
        enabled = True

    graph = SimpleNamespace(spec=SimpleNamespace(nodes=[_Node()]))

    assert _graph_consumes_bgem3_shots(graph) is False


def test_bgem3_warmup_is_excluded_from_item_latency() -> None:
    class _Provider:
        def encode(self, text: str, **kwargs: object) -> dict[str, object]:
            assert "evaluation warmup" in text
            assert kwargs == {"max_length": 1024, "return_sparse": True}
            return {"dense": [1.0]}

    report = _warm_bgem3_provider(_Provider())

    assert report is not None
    assert report["excluded_from_item_latency"] is True
    assert isinstance(report["duration_ms"], int)


def test_slice_requirement_spec_is_parsed() -> None:
    assert _parse_slice_requirement("career_site=100/25/0.85") == ("career_site", 100, 25, 0.85)


@pytest.mark.parametrize("spec", ["career_site", "career_site=100/25", "=100/25/0.85"])
def test_slice_requirement_rejects_a_malformed_spec(spec: str) -> None:
    with pytest.raises(ValueError):
        _parse_slice_requirement(spec)


def test_slice_gate_fails_a_dataset_that_cannot_measure_the_slice() -> None:
    """fixed140 carries 27 career-site items and 3 positives.

    A headline F1 of 0.88 on that dataset says nothing about career sites, which
    are ~88% of live traffic, so the gate must reject it as unmeasurable rather
    than pass on the telegram-dominated average.
    """
    by_source_kind = {
        "career_site": {"items": 27, "tp": 2, "fn": 1, "recall": 0.667, "precision": 1.0},
        "telegram_channel": {"items": 116, "tp": 13, "fn": 2, "recall": 0.867, "precision": 0.929},
    }

    checks = _evaluate_slice_requirements(
        ["career_site=100/25/0.85", "telegram_channel=50/10/0.80"], by_source_kind
    )

    career, telegram = checks
    assert career["passed"] is False
    assert "items 27 < 100" in career["reason"]
    assert "relevant 3 < 25" in career["reason"]
    assert "recall 0.667 < 0.850" in career["reason"]
    assert telegram["passed"] is True


def test_slice_gate_fails_when_the_slice_is_absent() -> None:
    checks = _evaluate_slice_requirements(["career_site=10/2/0.5"], {})
    assert checks[0]["passed"] is False
    assert checks[0]["reason"] == "slice_absent_from_dataset"


def test_slice_gate_passes_a_representative_slice() -> None:
    by_source_kind = {
        "career_site": {"items": 180, "tp": 30, "fn": 4, "recall": 0.882, "precision": 0.75}
    }
    checks = _evaluate_slice_requirements(["career_site=100/25/0.85"], by_source_kind)
    assert checks[0]["passed"] is True
    assert checks[0]["reason"] == "ok"


def test_require_slice_defaults_to_empty() -> None:
    sys.argv = ["run_pipeline_eval.py"]
    assert parse_args().require_slice == []
