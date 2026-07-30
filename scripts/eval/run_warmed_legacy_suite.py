"""Run legacy graph evals sequentially after one in-process BGE-M3 warm-up."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scripts.eval import run_pipeline_eval

_GRAPHS = {
    "control": "config/pipelines/asis_legacy_best.yaml",
    "h15": "config/pipelines/experiment_h15_post_decision_presentation.yaml",
    "h14": "config/pipelines/experiment_h14_late_canonicalization.yaml",
    "h11": "config/pipelines/experiment_h11_no_late_scoring.yaml",
    "h12": "config/pipelines/experiment_h12_uncertainty_router.yaml",
    "h13": "config/pipelines/experiment_h13_full_after_decision.yaml",
    "h16": "config/pipelines/experiment_h16_one_call_decision.yaml",
    "h17": "config/pipelines/experiment_h17_compact_aggregator.yaml",
    "h18": "config/pipelines/experiment_h18_compact_guarded_aggregator.yaml",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run selected legacy eval graphs in one warmed Python process."
    )
    parser.add_argument("--sample", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dataset", default="fixtures/dataset/eval_dataset.jsonl")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--graphs", nargs="+", choices=tuple(_GRAPHS), default=list(_GRAPHS))
    parser.add_argument(
        "--tenant-config", default="job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml"
    )
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--user-id", default="480637186")
    return parser.parse_args()


def _runner_argv(args: argparse.Namespace, graph_id: str, output: Path) -> list[str]:
    return [
        "run_pipeline_eval.py",
        "--dataset",
        args.dataset,
        "--sample",
        str(args.sample),
        "--seed",
        str(args.seed),
        "--graph",
        _GRAPHS[graph_id],
        "--state-mode",
        "runtime",
        "--profile-source",
        "tenant",
        "--tenant-config",
        args.tenant_config,
        "--tenant-id",
        args.tenant_id,
        "--user-id",
        args.user_id,
        "--no-langfuse",
        "--out",
        str(output),
        "--run-name",
        f"{graph_id}_warmed_n{args.sample}",
    ]


async def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider

    bgem3_provider = BgeMThreeProvider()

    original_argv = sys.argv
    failures: list[str] = []
    try:
        for graph_id in args.graphs:
            output = args.out_dir / f"{graph_id}_seed{args.seed}_n{args.sample}.json"
            sys.argv = _runner_argv(args, graph_id, output)
            exit_code = await run_pipeline_eval.main(bgem3_provider=bgem3_provider)
            if exit_code != 0 or not output.exists():
                failures.append(graph_id)
    finally:
        sys.argv = original_argv
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
