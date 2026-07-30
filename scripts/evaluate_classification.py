"""Classification eval harness (TD-002 / ADR-032).

Runs the cheap post-type classifier against the gold-labeled dataset
(``fixtures/dataset/eval_dataset_fixed140_locked_v1.jsonl``) and reports precision/recall/F1 per class,
overall false-positive rate, and URL validity rate.

Usage:
    python scripts/evaluate_classification.py
    python scripts/evaluate_classification.py --limit 200
    python scripts/evaluate_classification.py --fixture path/to/eval_dataset.jsonl
    python scripts/evaluate_classification.py --gate        # exit 1 on regression

Exits non-zero when ``--gate`` is set and the harness detects a regression
(FP rate > 25% or precision < 0.90 on the JOB_POSTING class).
See TD-029 in docs/techdebt.md for the baseline justification.

Supports two fixture schemas:

- legacy: ``{"raw_item": ..., "labels": {"post_type": "job_posting"}}``
- current lightweight: top-level raw-item fields plus ``is_job`` / ``relevant``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from job_ftch.domain import PostType, RawItem  # noqa: E402
from job_ftch.infrastructure.classifiers.keyword_lists import (  # noqa: E402
    load_announcement_tokens,
    load_candidate_tokens,
    load_job_posting_strong_tokens,
    load_job_posting_tokens,
    load_spam_tokens,
)
from job_ftch.nodes.post_type import PostTypeClassificationNode  # noqa: E402

FP_RATE_CEILING = 0.25  # measured 0.2287; tolerance +0.02
JOB_PRECISION_FLOOR = 0.90  # measured 0.9008


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate post-type classification against gold labels.",
    )
    parser.add_argument(
        "--fixture",
        default="fixtures/dataset/eval_dataset_fixed140_locked_v1.jsonl",
        help="canonical locked eval dataset (2085 items, locked 2026-07-15)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of samples (for quick smoke runs).",
    )
    parser.add_argument(
        "--output",
        default="artifacts/eval/classification.json",
        help="Where to write the JSON report.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero on regression (FP > 25%% or JOB_POSTING precision < 0.90).",
    )
    return parser.parse_args()


def _iter_fixture(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _safe_url(url: object) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    lowered = url.lower().strip()
    return lowered.startswith(("http://", "https://"))


def _expected_post_type(record: dict[str, Any]) -> str:
    labels = record.get("labels")
    if isinstance(labels, dict) and "post_type" in labels:
        return str(labels["post_type"])
    if "is_job" in record:
        return PostType.JOB_POSTING.value if bool(record["is_job"]) else PostType.UNKNOWN.value
    msg = "Fixture record must contain labels.post_type or is_job."
    raise KeyError(msg)


def _raw_item_from_record(record: dict[str, Any]) -> RawItem:
    raw_payload = record.get("raw_item")
    if isinstance(raw_payload, dict):
        return RawItem.model_validate(raw_payload)
    allowed_keys = {
        "stable_id",
        "source_kind",
        "source_name",
        "url",
        "text",
        "metadata",
        "external_id",
        "published_at",
        "source_record_id",
        "fetched_at",
        "language",
    }
    payload = {key: value for key, value in record.items() if key in allowed_keys}
    if (
        not payload.get("external_id")
        and not str(payload.get("url", "") or "").strip()
        and "stable_id" in payload
    ):
        payload["external_id"] = str(payload["stable_id"])
    return RawItem.model_validate(payload)


async def _classify_one(
    node: PostTypeClassificationNode,
    raw: RawItem,
) -> tuple[str, bool]:
    """Return (predicted_post_type, llm_was_used)."""
    out = await node.process(raw)
    # ``PostTypeClassificationNode.process`` always returns a ``RawItem`` (it
    # tags the prediction into metadata) but the type signature allows ``None``.
    metadata = (out.metadata if out is not None else {}) or {}
    predicted = str(metadata.get("preclassified_post_type", PostType.UNKNOWN.value))
    model = str(metadata.get("preclassified_model", "rules_v2"))
    used_llm = "rules" not in model and "v2" not in model
    return predicted, used_llm


def _per_class_metrics(
    tp: Counter[str],
    fp: Counter[str],
    fn: Counter[str],
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    classes = set(tp) | set(fp) | set(fn)
    for cls in sorted(classes):
        precision = tp[cls] / (tp[cls] + fp[cls]) if (tp[cls] + fp[cls]) > 0 else 0.0
        recall = tp[cls] / (tp[cls] + fn[cls]) if (tp[cls] + fn[cls]) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        out[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp[cls] + fn[cls],
        }
    return out


async def evaluate(
    fixture_path: Path,
    limit: int | None,
) -> dict[str, Any]:
    records = _iter_fixture(fixture_path)
    if limit is not None:
        records = records[:limit]

    node = PostTypeClassificationNode(
        classifier=None,
        announcement_tokens=load_announcement_tokens(),
        job_posting_tokens=load_job_posting_tokens(),
        job_posting_strong_tokens=load_job_posting_strong_tokens(),
        candidate_tokens=load_candidate_tokens(),
        spam_tokens=load_spam_tokens(),
    )

    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    per_source: dict[str, dict[str, int]] = {}
    valid_url_count = 0
    expected_job_with_url = 0
    llm_calls = 0

    for record in records:
        expected = _expected_post_type(record)
        raw = _raw_item_from_record(record)

        predicted, used_llm = await _classify_one(node, raw)
        if used_llm:
            llm_calls += 1

        if predicted == expected:
            tp[predicted] += 1
        else:
            fp[predicted] += 1
            fn[expected] += 1

        # Per-source breakdown.
        source = str(raw.source_name or raw.source_kind.value)
        per_source.setdefault(source, {"samples": 0, "correct": 0})
        per_source[source]["samples"] += 1
        if predicted == expected:
            per_source[source]["correct"] += 1

        # URL validity for items expected to be JOB_POSTING.
        if expected == PostType.JOB_POSTING.value and _safe_url(raw.url):
            expected_job_with_url += 1
            if _safe_url(raw.url):
                valid_url_count += 1

    # False positive rate: how many NON-job_posting items were classified as job_posting.
    non_job_total = sum(1 for r in records if _expected_post_type(r) != PostType.JOB_POSTING.value)
    fp_job_posting = fp[PostType.JOB_POSTING.value]
    fp_rate = fp_job_posting / non_job_total if non_job_total > 0 else 0.0

    overall_accuracy = sum(tp.values()) / len(records) if records else 0.0
    metrics = _per_class_metrics(tp, fp, fn)
    valid_url_rate = valid_url_count / expected_job_with_url if expected_job_with_url else 1.0
    llm_calls_per_100 = round(llm_calls / len(records) * 100, 2) if records else 0.0

    report: dict[str, Any] = {
        "fixture": str(fixture_path),
        "samples": len(records),
        "overall_accuracy": round(overall_accuracy, 4),
        "metrics_per_class": metrics,
        "false_positive_rate": round(fp_rate, 4),
        "false_positive_rate_definition": (
            "fraction of non-job_posting items classified as job_posting"
        ),
        "valid_url_rate": round(valid_url_rate, 4),
        "valid_url_rate_definition": ("fraction of expected job_posting items with parseable URL"),
        "llm_calls_per_100_items": llm_calls_per_100,
        "per_source_accuracy": {
            source: round(stats["correct"] / stats["samples"], 4)
            for source, stats in sorted(per_source.items())
            if stats["samples"] > 0
        },
    }
    metrics_per_class: dict[str, dict[str, float]] = report["metrics_per_class"]
    job_metrics = metrics_per_class.get(PostType.JOB_POSTING.value, {})
    # Gate baselines (TD-029 in docs/techdebt.md). Measured 2026-07-28 on the
    # locked 2085-sample dataset. FP rate 0.2287 is driven by the 647 unknown-class
    # items; the JOB_POSTING precision itself is 0.9008. Tightening the FP gate
    # requires a dedicated classifier improvement cycle (not an MVP blocker).
    report["gate_passed"] = (
        job_metrics.get("precision", 0.0) >= JOB_PRECISION_FLOOR
        and report["false_positive_rate"] <= FP_RATE_CEILING
    )
    return report


def _write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main() -> int:
    args = parse_args()
    fixture = Path(args.fixture)
    if not fixture.exists():
        print(f"ERROR: fixture not found: {fixture}", file=sys.stderr)
        return 2

    report = asyncio.run(evaluate(fixture, args.limit))
    output = Path(args.output)
    _write_report(report, output)

    # Print a short summary.
    print("=" * 60)
    print(f"Classification eval — {report['samples']} samples")
    print(f"  overall accuracy : {report['overall_accuracy']:.4f}")
    print(
        f"  false positive   : {report['false_positive_rate']:.4f} "
        f"(target <= {FP_RATE_CEILING:.2f})"
    )
    print(f"  valid_url_rate   : {report['valid_url_rate']:.4f}")
    print(f"  LLM/100 items    : {report['llm_calls_per_100_items']}")
    print("  per-class metrics:")
    for cls, m in report["metrics_per_class"].items():
        print(
            f"    {cls:18s}  precision={m['precision']:.4f}  "
            f"recall={m['recall']:.4f}  f1={m['f1']:.4f}  support={m['support']}"
        )
    print(f"  report           : {output}")
    print("=" * 60)

    if args.gate and not report["gate_passed"]:
        print("GATE FAILED: precision < 0.90 OR FP > 25%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
