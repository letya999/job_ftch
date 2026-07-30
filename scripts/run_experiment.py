"""M3 experiment runner: replay golden dataset through the FULL production pipeline.

Runs each raw_item from the golden dataset through the complete ~23-node pipeline
using real OpenAI LLM (gpt-5.4-nano) for extraction and scoring, but in-memory
stores to avoid infrastructure dependencies.

Emits per-node Langfuse spans, a categorical 'exit_stage' score, and LLM-judge
evaluator scores (hallucination, extraction_correctness) on every extracted job.

Usage:
    python scripts/run_experiment.py
    python scripts/run_experiment.py --sample 30 --no-eval
    python scripts/run_experiment.py --dataset-name job_ftch_m3 --out results/exp_full.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import sys
from collections import Counter
from contextlib import nullcontext, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import langfuse.api as lf_api
from dotenv import load_dotenv
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from job_ftch.application.contracts import SanitizingNode, Stage
    from job_ftch.config import Settings
    from job_ftch.domain import Job, JobDraft, ProfileCatalog

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")


def _langfuse_env() -> tuple[str, str, str]:
    public_key = os.environ.get("JOB_FTCH_LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("JOB_FTCH_LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("JOB_FTCH_LANGFUSE_HOST", "https://cloud.langfuse.com")
    return public_key, secret_key, host


# ── Built-in evaluation profile (ML engineer targeting RU/EN markets) ────────
_EVAL_PROFILE_TARGET_ROLES = (
    "ML Engineer",
    "Data Scientist",
    "NLP Engineer",
    "AI Engineer",
    "MLOps Engineer",
    "ML разработчик",
    "Data Engineer",
    "Research Engineer",
    "Machine Learning Engineer",
    "Deep Learning Engineer",
    "аналитик данных",
    "дата-сайентист",
    "инженер по машинному обучению",
)
_EVAL_PROFILE_HARD_REQUIREMENTS = (
    "python",
    "ml",
    "machine learning",
    "deep learning",
    "llm",
    "nlp",
    "pytorch",
    "tensorflow",
    "data science",
)
_EVAL_PROFILE_SOFT_PREFERENCES = (
    "rag",
    "transformers",
    "bert",
    "gpt",
    "embeddings",
    "mlops",
    "computer vision",
    "reinforcement learning",
    "langchain",
    "huggingface",
    "scikit",
    "keras",
    "spark",
    "airflow",
    "mlflow",
)


def _build_eval_catalog() -> ProfileCatalog:
    from job_ftch.domain.models import LanguageCode
    from job_ftch.domain.profile import ProfileCatalog, ProfileWeights, SearchProfile

    profile = SearchProfile(
        profile_id="ml_engineer_eval",
        name="ML Engineer (Eval)",
        target_roles=_EVAL_PROFILE_TARGET_ROLES,
        hard_requirements=_EVAL_PROFILE_HARD_REQUIREMENTS,
        soft_preferences=_EVAL_PROFILE_SOFT_PREFERENCES,
        allowed_languages=(LanguageCode.RU, LanguageCode.EN),
        relevance_threshold=0.45,
        weights=ProfileWeights(),
    )
    return ProfileCatalog(profiles=tuple([profile]))


def _build_full_pipeline() -> tuple[
    Settings, ProfileCatalog, tuple[SanitizingNode[Any], list[Stage[Any, Any]]]
]:
    """Build the full production node list using real settings + LLM, but in-memory stores."""
    from job_ftch.application.builder import build_llm, build_nodes, load_profile_catalog
    from job_ftch.config import get_settings
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore

    settings = get_settings()

    # Configure for in-memory replay
    settings.store_backend = "memory"
    settings.job_group_store_backend = "memory"
    settings.embedding_prefilter_enabled = False  # keep off - Qdrant has no replay data

    # Use the real production profile (same as run_full_e2e.py / run_diagnostics.py).
    # Without this, load_profile_catalog returns empty → fallback eval profile → wrong thresholds.
    if settings.filter_profile_path is None:
        from pathlib import Path as _Path

        settings.filter_profile_path = _Path("config/profiles/ai_jobs_ru_kz.yaml")

    catalog = load_profile_catalog(settings)
    if not catalog.profiles:
        catalog = _build_eval_catalog()

    llm = build_llm(settings)
    store = InMemoryStore()
    job_group_store = InMemoryJobGroupStore(settings)

    sanitize_node, _snapshot_filter, nodes = build_nodes(
        settings, store, llm, job_group_store, catalog
    )
    return settings, catalog, (sanitize_node, list(nodes))


# ── Per-item full pipeline replay ───────────────────────────────────────────


async def _run_item_full(
    raw_dict: dict[str, Any],
    sanitize_node: SanitizingNode[Any],
    nodes: list[Stage[Any, Any]],
    lf: Any | None = None,
) -> dict[str, Any]:
    """Replay item through ALL nodes. Return {exit_stage: str, extracted: JobDraft|None}."""
    from job_ftch.application.drops import RawItemDropped
    from job_ftch.domain import RawItem

    try:
        item = RawItem.model_validate(raw_dict)
    except Exception:
        return {"exit_stage": "invalid", "extracted": None}

    # Step 0: Sanitize
    try:
        current = await sanitize_node.process(item)
    except Exception:
        return {"exit_stage": "SanitizeNode_Error", "extracted": None}

    if current is None:
        return {"exit_stage": "SanitizeNode", "extracted": None}

    extracted_job: Any | None = None

    for node in nodes:
        stage_name = node.__class__.__name__
        # Outer span to ensure every node is traced in Langfuse
        span_cm = (
            lf.start_as_current_observation(name=stage_name, as_type="span")
            if lf
            else nullcontext()
        )
        with span_cm:
            try:
                result = await node.process(current)
                if result is None:
                    return {"exit_stage": stage_name, "extracted": extracted_job}
                current = result

                # Capture extraction result
                if stage_name == "ExtractionNode":
                    extracted_job = current

            except RawItemDropped:
                return {"exit_stage": stage_name, "extracted": extracted_job}
            except Exception:
                return {"exit_stage": f"{stage_name}_Error", "extracted": extracted_job}

    return {"exit_stage": "emitted", "extracted": current}


# ── LLM-judge Evaluators ─────────────────────────────────────────────────────

_HALLUCINATION_PROMPT = """You are a strict evaluator. Given the ORIGINAL job post text and the EXTRACTED structured job, return a JSON object {"hallucination": <float 0-1>, "reason": "..."}.
hallucination = fraction of extracted fields (company, salary, skills, location) that are NOT supported by the original text. 
0 = everything grounded, 1 = mostly invented.
Return ONLY valid JSON."""

_CORRECTNESS_PROMPT = """You are a strict evaluator. Given the ORIGINAL job post text and the EXTRACTED structured job, return a JSON object {"correctness": <float 0-1>, "reason": "..."}.
correctness = how accurately the key fields (title, company, skills, work_mode, salary) reflect the source. 
1 = perfect, 0 = wrong.
Return ONLY valid JSON."""


async def _eval_llm_judge(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    original_text: str,
    extracted_job: JobDraft | Job,
    key: str,
) -> tuple[float | None, str]:
    """Generic LLM-judge wrapper."""
    job_summary = {
        "title": getattr(extracted_job, "title_raw", getattr(extracted_job, "title", None)),
        "company": getattr(
            extracted_job, "company_name_raw", getattr(extracted_job, "company", None)
        ),
        "location": getattr(
            extracted_job, "location_raw", getattr(extracted_job, "location", None)
        ),
        "work_mode": str(extracted_job.work_mode),
        "compensation": extracted_job.compensation.model_dump()
        if extracted_job.compensation
        else None,
        "skills": [s.canonical_name for s in getattr(extracted_job, "skills_explicit", ())],
    }

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"ORIGINAL TEXT:\n{original_text}\n\nEXTRACTED JOB:\n{json.dumps(job_summary, ensure_ascii=False)}",
                },
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        score = data.get(key)
        reason = data.get("reason", "no reason provided")
        return (float(score) if score is not None else None, str(reason))
    except Exception as exc:
        return (None, f"eval_failed: {exc}")


# ── Metrics helpers ──────────────────────────────────────────────────────────

# Maps full-pipeline exit stages to the 3-stage golden labels for exit_stage_match score
GOLDEN_STAGES_MAPPING = {
    "HardFilterNode": "hard_filter",
    "SemanticPrefilterNode": "semantic_prefilter",
    "emitted": None,
}


def _compute_metrics(results: list[dict]) -> dict[str, Any]:  # type: ignore
    confusion: dict[tuple[str | None, str | None], int] = Counter()
    # Golden dataset expected labels
    STAGES = [None, "hard_filter", "semantic_prefilter"]
    STAGE_LABELS = {
        None: "ACCEPT",
        "hard_filter": "hard_filter",
        "semantic_prefilter": "semantic_prefilter",
    }

    for r in results:
        # Map actual exit to golden simplified stage
        actual_mapped = GOLDEN_STAGES_MAPPING.get(
            r["actual_exit"], "other_drop" if r["actual_exit"] != "emitted" else None
        )
        # For metric purposes, treat any drop after semantic_prefilter as ACCEPT (since golden only knows up to prefilter)
        if actual_mapped == "other_drop":
            # If it passed prefilter but dropped later, it's effectively an ACCEPT in M3's view
            actual_mapped = None

        confusion[(r["expected_exit"], actual_mapped)] += 1

    total = len(results)
    correct = sum(
        1
        for r in results
        if r["expected_exit"]
        == GOLDEN_STAGES_MAPPING.get(
            r["actual_exit"], None if r["actual_exit"] == "emitted" else "other_drop"
        )
    )

    per_class: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        tp = confusion.get((stage, stage), 0)
        fp = sum(confusion.get((other, stage), 0) for other in STAGES if other != stage)
        fn = sum(confusion.get((stage, other), 0) for other in STAGES if other != stage)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        support = sum(confusion.get((stage, other), 0) for other in STAGES)
        label = STAGE_LABELS[stage]
        per_class[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    return {
        "total": total,
        "accuracy": round(correct / total if total else 0.0, 4),
        "per_class": per_class,
        "confusion": {
            f"{STAGE_LABELS[e]}→{STAGE_LABELS[a]}": v for (e, a), v in confusion.items() if v > 0
        },
    }


def _print_report(metrics: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("  Full-Pipeline Experiment Results")
    print("=" * 60)
    print(f"  Total items : {metrics['total']}")
    print(f"  Accuracy    : {metrics['accuracy']:.1%}")
    print()
    print(f"  {'Class':<22} {'P':>6} {'R':>6} {'F1':>6} {'Sup':>6}")
    print("  " + "-" * 50)
    for label, m in metrics["per_class"].items():
        print(
            f"  {label:<22} {m['precision']:>6.3f} {m['recall']:>6.3f}"
            f" {m['f1']:>6.3f} {m['support']:>6}"
        )
    print()
    print("  Confusion (expected→actual_mapped):")
    for key, count in sorted(metrics["confusion"].items(), key=lambda x: -x[1]):
        print(f"    {key:<45} {count:>5}")
    print(f"{'=' * 60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full-pipeline experiment runner.")
    parser.add_argument("--golden", default="fixtures/dataset/labels.jsonl")
    parser.add_argument("--out", default=None, help="Save full results to JSON file.")
    parser.add_argument("--sample", type=int, default=0, help="Process only first N items.")
    parser.add_argument("--shuffle", action="store_true", help="Shuffle records before sampling.")
    parser.add_argument("--no-eval", action="store_true", help="Skip LLM-judge evaluators.")
    parser.add_argument(
        "--dataset-name",
        default="job_ftch_m3",
        help="Langfuse dataset name (default: job_ftch_m3).",
    )
    parser.add_argument(
        "--no-langfuse", action="store_true", help="Skip Langfuse upload even if keys are present."
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"ERROR: {golden_path} not found", file=sys.stderr)
        return 1

    records = [
        json.loads(line)
        for line in golden_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.shuffle:
        random.shuffle(records)
    if args.sample > 0:
        records = records[: args.sample]
    print(f"Loaded {len(records)} records from {golden_path}")

    # Build full pipeline
    settings, _catalog, (sanitize_node, nodes) = _build_full_pipeline()

    # LLM Client for evaluators
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    )
    eval_model = settings.openai_model  # gpt-5.4-nano

    # Optional Langfuse tracing
    public_key, secret_key, host = _langfuse_env()
    lf_instance = None
    run_name = f"full_pipeline_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}"

    if not args.no_langfuse and public_key and secret_key:
        try:
            from langfuse import Langfuse

            lf_instance = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        except ImportError:
            pass

    results: list[dict[str, Any]] = []
    eval_calls = 0
    _MAX_EVAL_CALLS = 1500

    for idx, rec in enumerate(records, 1):
        raw_dict = rec.get("raw_item", {})
        expected = rec.get("expected", {})
        expected_exit = expected.get("exit_stage")  # None | "hard_filter" | "semantic_prefilter"
        stable_id = raw_dict.get("stable_id", "")

        if lf_instance is not None:
            obs_ctx: Any = lf_instance.start_as_current_observation(
                name="pipeline_item",
                as_type="span",
                input={
                    "stable_id": stable_id,
                    "source_name": raw_dict.get("source_name", ""),
                    "source_kind": raw_dict.get("source_kind", ""),
                },
                metadata={
                    "run_name": run_name,
                    "source_kind": raw_dict.get("source_kind", ""),
                    "post_type": expected.get("post_type", ""),
                    "expected_exit": str(expected_exit),
                },
            )
        else:
            obs_ctx = nullcontext()

        trace_id = None
        with obs_ctx:
            res = await _run_item_full(
                raw_dict,
                sanitize_node,
                nodes,
                lf=lf_instance,
            )
            actual_exit = res["exit_stage"]
            extracted_job = res["extracted"]

            # 1. Exit stage match score (simplified for golden labels)
            actual_mapped = GOLDEN_STAGES_MAPPING.get(
                actual_exit, None if actual_exit == "emitted" else "other_drop"
            )
            if actual_mapped == "other_drop":
                actual_mapped = None  # Passed prefilter -> M3 ACCEPT

            match = expected_exit == actual_mapped

            if lf_instance is not None:
                lf_instance.update_current_span(
                    output={
                        "expected_exit": str(expected_exit),
                        "actual_exit": actual_exit,
                        "actual_mapped": str(actual_mapped),
                        "match": match,
                    }
                )

                # Categorical funnel score
                lf_instance.score_current_trace(
                    name="exit_stage", value=actual_exit, data_type="CATEGORICAL"
                )

                # Metric score
                lf_instance.score_current_trace(
                    name="exit_stage_match",
                    value=1.0 if match else 0.0,
                    comment=f"expected={expected_exit} actual={actual_exit}",
                )

                # 2. LLM-judge evaluators
                if not args.no_eval and extracted_job and eval_calls < _MAX_EVAL_CALLS:
                    original_text = raw_dict.get("text", "")

                    # Hallucination
                    h_score, h_reason = await _eval_llm_judge(
                        openai_client,
                        eval_model,
                        _HALLUCINATION_PROMPT,
                        original_text,
                        extracted_job,
                        "hallucination",
                    )
                    if h_score is not None:
                        lf_instance.score_current_trace(
                            name="hallucination",
                            value=h_score,
                            comment=h_reason,
                        )

                    # Extraction Correctness
                    c_score, c_reason = await _eval_llm_judge(
                        openai_client,
                        eval_model,
                        _CORRECTNESS_PROMPT,
                        original_text,
                        extracted_job,
                        "correctness",
                    )
                    if c_score is not None:
                        lf_instance.score_current_trace(
                            name="extraction_correctness",
                            value=c_score,
                            comment=c_reason,
                        )

                    eval_calls += 1

                trace_id = lf_instance.get_current_trace_id()

        # Link to dataset run
        if lf_instance is not None and stable_id and trace_id:
            with suppress(Exception):
                lf_instance.api.dataset_run_items.create(
                    request=lf_api.CreateDatasetRunItemRequest(
                        run_name=run_name,
                        dataset_item_id=stable_id,
                        trace_id=trace_id,
                        run_description="Full production pipeline (~23 nodes) replay + LLM-judge evaluators",
                        metadata={"scorer": "full_pipeline_v1", "nodes": len(nodes) + 1},
                    )
                )

        results.append(
            {
                "stable_id": stable_id,
                "source_name": raw_dict.get("source_name", ""),
                "source_kind": raw_dict.get("source_kind", ""),
                "post_type": expected.get("post_type", ""),
                "expected_exit": expected_exit,
                "actual_exit": actual_exit,
                "match": match,
            }
        )

        if idx % 10 == 0 or idx == len(records):
            print(f"  [{idx}/{len(records)}] processed (eval_calls={eval_calls}) ...")

    metrics = _compute_metrics(results)
    _print_report(metrics)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_at": datetime.now(UTC).isoformat(),
            "golden": str(golden_path),
            "metrics": metrics,
            "results": results,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Results saved to {out_path}")

    if lf_instance is not None:
        lf_instance.flush()
        print(f"  [Langfuse] Logged {len(results)} items, run={run_name!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
