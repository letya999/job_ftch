"""Direct shot-margin eval on the same 400-sample (seed=42) the pipeline uses.

Pure profile-driven relevance: score each item by BGE-M3 shot margin, sweep
threshold, report P/R/F1 and the implied LLM-band size (items near the
boundary). No keyword classify(), no LLM calls. Answers: can the 40 shots
alone hit P>=0.90 and R>=0.70, and how narrow is the uncertain band?
"""

from __future__ import annotations

import json
import os
import random

import numpy as np
from FlagEmbedding import BGEM3FlagModel

from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeShotScorer,
    BgeMThreeShotStore,
)

DATASET = "fixtures/dataset/eval_dataset.jsonl"


def main() -> None:
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    # Replicate the pipeline eval's exact 400-sample.
    rng = random.Random(42)
    sample = rng.sample(rows, min(400, len(rows)))
    gold = np.array([1 if r.get("relevant") == 1 else 0 for r in sample])
    print(f"sample={len(sample)} relevant={int(gold.sum())} not={int((1 - gold).sum())}")

    url = os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333")
    store = BgeMThreeShotStore(url=url, collection="profile_shots_bgem3")
    pos, neg, _, _ = store.load()
    scorer = BgeMThreeShotScorer(pos, neg, top_k=5)

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    texts = [(r.get("text") or "")[:3000] for r in sample]
    out = model.encode(texts, batch_size=8, max_length=1024, return_dense=True, return_sparse=False)
    margins = np.array(
        [scorer.score_vector(np.asarray(d, dtype=np.float32)).margin for d in out["dense_vecs"]]
    )

    rel_m = margins[gold == 1]
    irr_m = margins[gold == 0]
    print(
        f"RELEVANT   margin: mean={rel_m.mean():+.3f} min={rel_m.min():+.3f} p10={np.percentile(rel_m, 10):+.3f}"
    )
    print(
        f"IRRELEVANT margin: mean={irr_m.mean():+.3f} max={irr_m.max():+.3f} p90={np.percentile(irr_m, 90):+.3f}"
    )

    # Sweep single threshold.
    print("\n=== single-threshold sweep ===")
    best = None
    for t in np.arange(-0.05, 0.18, 0.002):
        pred = (margins >= t).astype(int)
        tp = int(((pred == 1) & (gold == 1)).sum())
        fp = int(((pred == 1) & (gold == 0)).sum())
        fn = int(((pred == 0) & (gold == 1)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if p >= 0.90 and r >= 0.70 and (best is None or f1 > best[-1]):
            best = (t, p, r, tp, fp, fn, f1)
    if best:
        t, p, r, tp, fp, fn, f1 = best
        print(
            f"  TARGET MET @ thr={t:.3f}: P={p:.3f} R={r:.3f} F1={f1:.3f} (TP={tp} FP={fp} FN={fn})"
        )
    else:
        print("  no single threshold meets P>=0.90 AND R>=0.70")

    # Best F1 overall + implied two-sided band for LLM.
    rows_out = []
    for t in np.arange(-0.05, 0.18, 0.002):
        pred = (margins >= t).astype(int)
        tp = int(((pred == 1) & (gold == 1)).sum())
        fp = int(((pred == 1) & (gold == 0)).sum())
        fn = int(((pred == 0) & (gold == 1)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        rows_out.append((t, p, r, f1))
    bt = max(rows_out, key=lambda x: x[3])
    print(f"  best-F1 @ thr={bt[0]:.3f}: P={bt[1]:.3f} R={bt[2]:.3f} F1={bt[3]:.3f}")

    # Two-sided band: low = highest thr keeping R>=0.95 ; high = lowest thr keeping P>=0.95.
    low = max((t for t, p, r, f in rows_out if r >= 0.95), default=None)
    high = min((t for t, p, r, f in rows_out if p >= 0.95), default=None)
    if low is not None and high is not None:
        band = ((margins >= low) & (margins < high)).sum()
        print("\n=== two-sided band (Layer C / LLM only) ===")
        print(f"  low(R>=0.95)={low:.3f}  high(P>=0.95)={high:.3f}")
        print(
            f"  items in band = {int(band)}/{len(sample)} ({100 * band / len(sample):.1f}%) -> LLM call budget"
        )
        print(f"  below low -> auto-reject: {int((margins < low).sum())}")
        print(f"  >= high  -> auto-accept: {int((margins >= high).sum())}")


if __name__ == "__main__":
    main()
