"""Binary relevance evaluation: decision engine vs gold labels -> P/R/F1.

The system under test is the DB-backed e5 shot-anchor relevance gate
(``ShotRelevanceScorer``). For each posting it produces a margin score; a
calibrated threshold turns it into a binary keep/drop decision. We compare that
decision against the independent gold ``relevant`` label and report discrete
Precision / Recall / F1, overall and sliced by source_kind / source_name.

This is node-agnostic: it measures the relevance decision (the part we are
fixing), not which pipeline node acts, and it does not depend on the external
extraction LLM. The decision engine (e5 bi-encoder) and the labeller
(bge-reranker cross-encoder + GPT ai_relevance) are different model families, so
the metric is not circular.

Threshold is calibrated honestly: best-F1 threshold is chosen on a train split
and the reported metrics are computed on a held-out test split (seeded).

Usage:
    python scripts/eval/run_eval.py --gold fixtures/dataset/gold_eval.jsonl --seed 42
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

from job_ftch.infrastructure.relevance.shot_anchor import (
    ShotEmbedder,
    ShotRelevanceScorer,
    ShotStore,
)

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1


def _confusion(labels: list[int], preds: list[int]) -> tuple[int, int, int, int]:
    tp = sum(1 for y, p in zip(labels, preds, strict=False) if y == 1 and p == 1)
    fp = sum(1 for y, p in zip(labels, preds, strict=False) if y == 0 and p == 1)
    fn = sum(1 for y, p in zip(labels, preds, strict=False) if y == 1 and p == 0)
    tn = sum(1 for y, p in zip(labels, preds, strict=False) if y == 0 and p == 0)
    return tp, fp, fn, tn


def _best_threshold(margins: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    best_t, best_f1 = 0.0, -1.0
    for t in np.unique(margins):
        preds = (margins >= t).astype(int)
        tp = int(((labels == 1) & (preds == 1)).sum())
        fp = int(((labels == 0) & (preds == 1)).sum())
        fn = int(((labels == 1) & (preds == 0)).sum())
        _, _, f1 = _prf(tp, fp, fn)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t, best_f1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="fixtures/dataset/gold_eval.jsonl")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--conf",
        choices=["all", "high"],
        default="all",
        help="Restrict to high-confidence labels for the trust-headline number.",
    )
    ap.add_argument("--out", default="results/eval_relevance.json")
    args = ap.parse_args()

    rows = [
        json.loads(line)
        for line in Path(args.gold).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.conf == "high":
        rows = [r for r in rows if r.get("confidence") == "high"]
    print(f"Gold items: {len(rows)} (conf={args.conf})")

    embedder = ShotEmbedder()
    store = ShotStore(
        url=os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333"),
        api_key=os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None,
    )
    pos, neg = store.load()
    scorer = ShotRelevanceScorer(pos, neg, embedder)

    margins = np.array([scorer.score_text(r["text"]).margin for r in rows], dtype=np.float32)
    labels = np.array([int(r["relevant"]) for r in rows], dtype=int)

    # Honest split: calibrate threshold on train, report on test.
    rng = np.random.default_rng(args.seed)
    idx = rng.permutation(len(rows))
    cut = len(rows) // 2
    tr, te = idx[:cut], idx[cut:]
    thr, tr_f1 = _best_threshold(margins[tr], labels[tr])
    preds_te = (margins[te] >= thr).astype(int)
    tp, fp, fn, tn = _confusion(list(labels[te]), list(preds_te))
    p, r, f1 = _prf(tp, fp, fn)

    # Also full-set metrics at the train-derived threshold (for the cuts).
    preds_all = (margins >= thr).astype(int)

    print(f"\nCalibrated threshold (train best-F1): {thr:+.4f} (train F1={tr_f1:.3f})")
    print(f"=== HELD-OUT TEST (n={len(te)}) ===")
    print(f"  Precision={p:.3f}  Recall={r:.3f}  F1={f1:.3f}")
    print(f"  confusion: TP={tp} FP={fp} FN={fn} TN={tn}")

    # full-set confusion + cuts
    tpA, fpA, fnA, tnA = _confusion(list(labels), list(preds_all))
    pA, rA, f1A = _prf(tpA, fpA, fnA)
    print(f"\n=== FULL SET (n={len(rows)}, same threshold) ===")
    print(
        f"  Precision={pA:.3f}  Recall={rA:.3f}  F1={f1A:.3f}  (TP={tpA} FP={fpA} FN={fnA} TN={tnA})"
    )

    def cut_report(key: str) -> dict[str, Any]:
        groups: dict[str, list[int]] = defaultdict(list)
        for i, row in enumerate(rows):
            groups[row.get(key) or "?"].append(i)
        out = {}
        print(f"\n  by {key}:")
        for g, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            yl = [int(labels[i]) for i in ids]
            yp = [int(preds_all[i]) for i in ids]
            tp, fp, fn, tn = _confusion(yl, yp)
            pp, rr_, ff = _prf(tp, fp, fn)
            out[g] = {
                "n": len(ids),
                "precision": round(pp, 3),
                "recall": round(rr_, 3),
                "f1": round(ff, 3),
            }
            print(f"    {g:20} n={len(ids):4} P={pp:.3f} R={rr_:.3f} F1={ff:.3f} (pos={sum(yl)})")
        return out

    sk = cut_report("source_kind")
    sn = cut_report("source_name")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(
            {
                "n": len(rows),
                "threshold": thr,
                "test": {
                    "n": len(te),
                    "precision": p,
                    "recall": r,
                    "f1": f1,
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "tn": tn,
                },
                "full": {
                    "precision": pA,
                    "recall": rA,
                    "f1": f1A,
                    "tp": tpA,
                    "fp": fpA,
                    "fn": fnA,
                    "tn": tnA,
                },
                "by_source_kind": sk,
                "by_source_name": sn,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
