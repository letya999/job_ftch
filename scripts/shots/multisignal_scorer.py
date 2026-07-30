"""Multi-signal relevance for a UNIVERSAL aggregator: combiner is derived ONLY
from shots, never from eval labels. The 400-sample is measurement-only.

Signals per text x (all shot-derived):
  a = topk cos(x, R+)  b = topk cos(x, R-)   # resume axis
  c = topk cos(x, V+)  d = topk cos(x, V-)   # vacancy axis
  sV = sparse-disc(x, vacancy pool)          # lexical, vacancy
  sR = sparse-disc(x, resume pool)           # lexical, resume
  penV = relu(d-c)^2   penR = relu(b-a)^2    # conjunctive penalties
  kwAnti, kwPos = derived-keyword hits

Approach A (NO training): fixed convex formula, principled weights.
Approach B (train in-the-moment on SHOTS): each shot's features computed
leave-one-out vs the other 39 shots; fit a small logreg on those 40 labeled
feature-vectors -> weights; apply to the 400. Per-user generalisable (refit when
a user uploads their own 40 shots). Eval labels never used for fitting.

Usage: uv run --env-file .env.dev --extra bgem3 python scripts/shots/multisignal_scorer.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from FlagEmbedding import BGEM3FlagModel
from sklearn.linear_model import LogisticRegression

if TYPE_CHECKING:
    from numpy.typing import NDArray

ROOT = Path(__file__).parent.parent.parent
DATASET = ROOT / "fixtures" / "dataset" / "eval_dataset.jsonl"
SHOTS = ROOT / "fixtures" / "shots" / "all_shots.jsonl"
KW = ROOT / "fixtures" / "shots" / "derived_keywords.json"

FEAT_NAMES = [
    "a:cosR+",
    "b:cosR-",
    "c:cosV+",
    "d:cosV-",
    "sV",
    "sR",
    "penV",
    "penR",
    "kwAnti",
    "kwPos",
]


def topk_mean(sims: np.ndarray, k: int = 5) -> float:
    return float(np.sort(sims)[::-1][:k].mean()) if sims.size else 0.0


def sweep(score: np.ndarray, gold: np.ndarray, label: str) -> None:
    best_f1 = best_t = None
    rows_o = []
    lo, hi = score.min(), score.max()
    for t in np.linspace(lo, hi, 400):
        pred = (score >= t).astype(int)
        tp = int(((pred == 1) & (gold == 1)).sum())
        fp = int(((pred == 1) & (gold == 0)).sum())
        fn = int(((pred == 0) & (gold == 1)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        rows_o.append((t, p, r, f1))
        if best_f1 is None or f1 > best_f1[3]:
            best_f1 = (t, p, r, f1)
        if p >= 0.90 and r >= 0.70 and (best_t is None or f1 > best_t[3]):
            best_t = (t, p, r, f1)
    print(f"--- {label} ---")
    print(f"  best-F1: P={best_f1[1]:.3f} R={best_f1[2]:.3f} F1={best_f1[3]:.3f}")  # type: ignore
    if best_t:
        print(f"  TARGET MET: P={best_t[1]:.3f} R={best_t[2]:.3f} F1={best_t[3]:.3f}")
    else:
        print("  target P>=.90&R>=.70 not met by single threshold alone")
    low = max((t for t, p, r, f in rows_o if r >= 0.95), default=None)
    high = min((t for t, p, r, f in rows_o if p >= 0.95), default=None)
    if low is not None and high is not None and low <= high:
        band = int(((score >= low) & (score < high)).sum())
        auto_acc = score >= high
        below = score < low
        tp = int((auto_acc & (gold == 1)).sum()) + int(((~below) & (~auto_acc) & (gold == 1)).sum())
        fn = int((below & (gold == 1)).sum())
        rc = tp / (tp + fn) if tp + fn else 0.0
        print(
            f"  band={band}/400 ({100 * band / 400:.0f}%)  auto-reject={int(below.sum())} "
            f"auto-accept={int(auto_acc.sum())}  LLM-ceiling R={rc:.3f}"
        )


def main() -> None:
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    rng = random.Random(42)
    sample = rng.sample(rows, 400)
    gold = np.array([1 if r.get("relevant") == 1 else 0 for r in sample])

    shots = []
    with open(SHOTS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                shots.append(json.loads(line))
    shot_label = np.array([1 if s["label"] == "positive" else 0 for s in shots])

    if KW.exists():
        with KW.open(encoding="utf-8") as handle:
            kw = json.load(handle)
    else:
        kw = {}
    pos_toks = [t.lower() for t in kw.get("positive_tokens", [])]
    anti_pats = [t.lower() for t in kw.get("anti_patterns", [])]

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    senc = model.encode(
        [s["text"] for s in shots],
        batch_size=8,
        max_length=1024,
        return_dense=True,
        return_sparse=True,
    )
    svecs = [np.asarray(v, dtype=np.float32) for v in senc["dense_vecs"]]
    s_sparse = [{int(t): float(w) for t, w in d.items()} for d in senc["lexical_weights"]]

    idx_of = {
        (kind, label): [i for i, s in enumerate(shots) if s["kind"] == kind and s["label"] == label]
        for kind in ("resume", "vacancy")
        for label in ("positive", "negative")
    }

    def disc(pos_idx: list[int], neg_idx: list[int]) -> dict[int, float]:
        d: dict[int, float] = {}
        for i in pos_idx:
            for k, v in s_sparse[i].items():
                d[k] = d.get(k, 0.0) + v
        for i in neg_idx:
            for k, v in s_sparse[i].items():
                d[k] = d.get(k, 0.0) - v
        return d

    def sparse_score(q: dict[int, float], dd: dict[int, float]) -> float:
        return sum(v * dd.get(t, 0.0) for t, v in q.items())

    def features(
        vec: NDArray[np.float32],
        sp: dict[int, float],
        text: str,
        exclude: int | None = None,
    ) -> list[float]:
        """Feature vector vs shot pools, optionally excluding one shot index (LOO)."""

        def pdv(kind: str, label: str) -> NDArray[np.float32]:
            ids = [i for i in idx_of[(kind, label)] if i != exclude]
            return np.vstack([svecs[i] for i in ids]) if ids else np.zeros((0, 1024), np.float32)

        Rp, Rn, Vp, Vn = (
            pdv("resume", "positive"),
            pdv("resume", "negative"),
            pdv("vacancy", "positive"),
            pdv("vacancy", "negative"),
        )
        a, b = topk_mean(Rp @ vec), topk_mean(Rn @ vec)
        c, d = topk_mean(Vp @ vec), topk_mean(Vn @ vec)
        dv = disc(
            [i for i in idx_of[("vacancy", "positive")] if i != exclude],
            [i for i in idx_of[("vacancy", "negative")] if i != exclude],
        )
        dr = disc(
            [i for i in idx_of[("resume", "positive")] if i != exclude],
            [i for i in idx_of[("resume", "negative")] if i != exclude],
        )
        sV, sR = sparse_score(sp, dv), sparse_score(sp, dr)
        penV, penR = max(0.0, d - c) ** 2, max(0.0, b - a) ** 2
        tl = text.lower()
        kwA = sum(1 for p in anti_pats if p in tl)
        kwP = sum(1 for p in pos_toks if p in tl)
        return [a, b, c, d, sV, sR, penV, penR, kwA, kwP]

    # Item features (vs all shots).
    texts = [(r.get("text") or "")[:3000] for r in sample]
    enc = model.encode(texts, batch_size=8, max_length=1024, return_dense=True, return_sparse=True)
    qd = [np.asarray(v, dtype=np.float32) for v in enc["dense_vecs"]]
    qs = [{int(t): float(w) for t, w in d.items()} for d in enc["lexical_weights"]]
    Xi: NDArray[np.float64] = np.asarray(
        [features(qd[i], qs[i], texts[i]) for i in range(len(sample))],
        dtype=np.float64,
    )

    # === Approach A: fixed formula (no training) ===
    # standardize features by item-set stats (deploy-time these come from shots; ok for measure)
    mu: NDArray[np.float64] = np.asarray(Xi.mean(0), dtype=np.float64)
    sd: NDArray[np.float64] = np.asarray(Xi.std(0), dtype=np.float64) + 1e-9
    Z: NDArray[np.float64] = (Xi - mu) / sd
    # principled weights: reward pos-axis alignment, punish neg-axis + penalties + anti-kw
    w_fixed = np.array([+1, -1, +1, -1, +0.5, +0.5, -1, -1, -1, +0.5], dtype=float)
    score_A = Z @ w_fixed
    sweep(score_A, gold, "A: fixed formula (no training)")

    # === Approach B: train logreg on 40 shots (leave-one-out features) ===
    Xs: NDArray[np.float64] = np.asarray(
        [features(svecs[i], s_sparse[i], shots[i]["text"], exclude=i) for i in range(len(shots))],
        dtype=np.float64,
    )
    Xs_z: NDArray[np.float64] = (Xs - mu) / sd  # same scaling as items
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    clf.fit(Xs_z, shot_label)
    score_B = clf.predict_proba(Z)[:, 1]
    sweep(score_B, gold, "B: trained on 40 shots (leave-one-out)")
    print("\n  B learned weights (from shots only):")
    for n, wv in sorted(zip(FEAT_NAMES, clf.coef_[0], strict=False), key=lambda kv: -abs(kv[1])):
        print(f"    {n:8} {wv:+.3f}")


if __name__ == "__main__":
    main()
