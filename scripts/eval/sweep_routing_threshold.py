"""Offline threshold sweep over a captured pipeline_eval results JSON.

Routing is the final, deterministic node: given the per-item scores already
stored in metadata, we can simulate any accept rule WITHOUT re-running the
12-minute pipeline. This finds the gate that maximises overall F1.

A gate only affects items that reached routing (exit_stage == "emitted").
Items dropped upstream stay negative predictions (the gate cannot recover
them), so their gold_relevant==1 cases are a permanent recall ceiling.

Usage:
    python scripts/eval/sweep_routing_threshold.py [results.json]
If no path is given, the most recent results/pipeline_eval_*.json is used.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from typing import Any


def _latest_results() -> Path:
    files = sorted(glob.glob("results/pipeline_eval_*.json"))
    if not files:
        raise SystemExit("No results/pipeline_eval_*.json found.")
    return Path(files[-1])


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _confusion(results: list[dict[str, Any]], predict_positive) -> tuple[int, int, int, int]:  # type: ignore
    tp = fp = fn = tn = 0
    for row in results:
        gold = int(row["gold_relevant"]) == 1
        pos = predict_positive(row)
        if pos and gold:
            tp += 1
        elif pos and not gold:
            fp += 1
        elif not pos and gold:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def _score(row: dict[str, Any], key: str) -> float | None:
    scores = row.get("scores") or {}
    value = scores.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _sweep(results: list[dict[str, Any]], key: str, label: str) -> dict[str, Any]:
    """Sweep a threshold on `key`. Emitted items accept iff score >= T;
    items with no score fall back to their original pipeline_accepted."""
    emitted = [r for r in results if r["exit_stage"] == "emitted"]
    candidate_scores = sorted({s for r in emitted if (s := _score(r, key)) is not None})
    if not candidate_scores:
        return {"label": label, "best_f1": 0.0, "threshold": None, "note": "no scores"}

    # candidate thresholds: midpoints + below-min + above-max
    thresholds = [candidate_scores[0] - 1e-6]
    for a, b in zip(candidate_scores, candidate_scores[1:], strict=False):
        thresholds.append((a + b) / 2)
    thresholds.append(candidate_scores[-1] + 1e-6)

    best: dict[str, Any] = {"label": label, "best_f1": -1.0, "threshold": None}
    for t in thresholds:

        def predict(row: dict[str, Any], _t: float = t) -> bool:
            if row["exit_stage"] != "emitted":
                return False
            s = _score(row, key)
            if s is None:
                return bool(row["pipeline_accepted"])
            return s >= _t

        tp, fp, fn, tn = _confusion(results, predict)
        p, r, f1 = _f1(tp, fp, fn)
        if f1 > best["best_f1"]:
            best = {
                "label": label,
                "best_f1": f1,
                "precision": p,
                "recall": r,
                "threshold": t,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            }
    return best


def _describe(results: list[dict[str, Any]], key: str) -> None:
    emitted = [r for r in results if r["exit_stage"] == "emitted"]
    rel = sorted(
        s for r in emitted if int(r["gold_relevant"]) == 1 and (s := _score(r, key)) is not None
    )
    irr = sorted(
        s for r in emitted if int(r["gold_relevant"]) == 0 and (s := _score(r, key)) is not None
    )

    def stats(xs: list[float]) -> str:
        if not xs:
            return "n=0"
        mid = xs[len(xs) // 2]
        return f"n={len(xs)} min={xs[0]:.4f} median={mid:.4f} max={xs[-1]:.4f}"

    print(f"\n  [{key}] among items reaching routing:")
    print(f"    relevant:   {stats(rel)}")
    print(f"    irrelevant: {stats(irr)}")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_results()
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload["results"]

    tp, fp, fn, tn = _confusion(results, lambda r: bool(r["pipeline_accepted"]))
    p, r, f1 = _f1(tp, fp, fn)
    relevant_total = sum(1 for x in results if int(x["gold_relevant"]) == 1)
    relevant_at_routing = sum(
        1 for x in results if x["exit_stage"] == "emitted" and int(x["gold_relevant"]) == 1
    )

    print(f"=== SWEEP over {path.name} ===")
    print(f"Baseline: TP={tp} FP={fp} FN={fn} TN={tn} | P={p:.3f} R={r:.3f} F1={f1:.3f}")
    print(
        f"Relevant total={relevant_total}, reaching routing={relevant_at_routing} "
        f"(recall ceiling at routing = {relevant_at_routing / relevant_total:.3f})"
    )

    _describe(results, "bge_reranker_max_score")
    _describe(results, "parallel_final_score")

    print("\n=== BEST GATE PER STRATEGY ===")
    candidates = [
        _sweep(results, "bge_reranker_max_score", "reranker"),
        _sweep(results, "parallel_final_score", "parallel"),
        _sweep(results, "parallel_dense_margin", "dense_margin"),
        _sweep(results, "parallel_score_role", "role"),
    ]
    candidates.sort(key=lambda c: c["best_f1"], reverse=True)
    for c in candidates:
        if c.get("threshold") is None:
            print(f"  {c['label']:14} -> {c.get('note', 'n/a')}")
            continue
        print(
            f"  {c['label']:14} -> F1={c['best_f1']:.3f} "
            f"P={c['precision']:.3f} R={c['recall']:.3f} "
            f"T={c['threshold']:.4f} (TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']})"
        )

    best = candidates[0]
    print(
        f"\nRECOMMENDED: gate on '{best['label']}' at T={best['threshold']:.4f} "
        f"-> F1={best['best_f1']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
