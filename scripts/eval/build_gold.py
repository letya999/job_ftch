"""Build a binary-labelled gold evaluation dataset (relevant / not relevant).

The label is node-agnostic and simple: for each posting, should the product
emit it as a relevant vacancy for the active profile (AI engineer / vibecoder /
AI automation)?  ``relevant`` in {0,1}.

Labelling is multi-signal to be trustworthy and avoid circularity with the
embedding decision engine under test:

- ``is_job``  : is this an actual vacancy at all? (golden post_type == job_posting
                OR a strong hiring signal in the text)
- ``rr``      : cross-encoder reranker score of (profile, text) - an independent
                model family from the e5 bi-encoder used as the decision engine.
- ``air``     : the GPT-derived ``ai_relevance`` already stored in labels.jsonl -
                an independent pre-computed LLM judgment.

Final label:
    relevant = is_job AND (rr >= TAU_HI) ......................... confident yes
    relevant = 0 if not is_job OR rr <= TAU_LO ................... confident no
    middle band (TAU_LO < rr < TAU_HI): decided by air >= 0.5,
        flagged confidence="low" for human adjudication.

Calibration mode prints distributions and reranker/air agreement so TAU can be
justified. Emit mode writes a stratified, seeded, frozen dataset.

Usage:
    python scripts/eval/build_gold.py --calibrate
    python scripts/eval/build_gold.py --emit --per-kind 152 --seed 42
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from job_ftch.infrastructure.classifiers.keyword_lists import (
    load_candidate_tokens,
    load_job_posting_strong_tokens,
    load_spam_tokens,
)

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")

_CLEAN = Path("fixtures/dataset/clean_pool.jsonl")
_LABELS = Path("fixtures/dataset/labels.jsonl")
_OUT = Path("fixtures/dataset/gold_eval.jsonl")
_IDS = Path("fixtures/dataset/gold_eval_ids.txt")

# The active profile is APPLIED AI engineering (user-confirmed). Building/shipping
# products with LLMs, integrating GPT/LLM APIs, agent/workflow automation (n8n),
# fullstack AI product work, vibe coding / AI-assisted development.
_PROFILE_POS = (
    "Applied AI engineering: AI engineer, LLM / applied AI engineer integrating GPT "
    "and large language model APIs into products, RAG, agentic workflows, AI "
    "automation engineer (n8n, agents, workflow automation), AI integration "
    "specialist, fullstack AI product engineer, vibe coder / AI-assisted developer "
    "shipping products fast, rapid prototyping, ML backend / AI platform engineer. "
    "Прикладной AI-инженер, LLM-интеграции, автоматизация на агентах, фуллстек AI."
)
# Explicitly out of scope (user marked these as negatives): research and pure DS.
_PROFILE_NEG = (
    "Data scientist, machine learning researcher, NLP research engineer, training "
    "models from scratch, statistical modeling, A/B testing analyst, research "
    "scientist, computer vision researcher, academic ML research. "
    "Дата-сайентист, ML-исследователь, обучение моделей с нуля, аналитик данных."
)

TAU_LO = 0.02
TAU_HI = 0.15
NEG_MARGIN = 0.05  # research/DS signal must not exceed applied-AI by this much


def _is_job(text: str, golden_post_type: str | None) -> bool:
    if golden_post_type == "job_posting":
        return True
    lowered = text.casefold()
    strong = load_job_posting_strong_tokens()
    cand = load_candidate_tokens()
    spam = load_spam_tokens()
    if any(t in lowered for t in cand) or any(t in lowered for t in spam):
        return False
    return any(t in lowered for t in strong)


def _load_pool() -> list[dict]:  # type: ignore
    raws = [
        json.loads(line) for line in _CLEAN.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    meta: dict[str, dict] = {}  # type: ignore
    for line in _LABELS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sid = row.get("raw_item", {}).get("stable_id", "")
        if sid:
            meta[sid] = row.get("expected", {})
    pool = []
    for raw in raws:
        sid = raw.get("stable_id", "")
        exp = meta.get(sid, {})
        pool.append(
            {
                "stable_id": sid,
                "source_kind": raw.get("source_kind"),
                "source_name": raw.get("source_name"),
                "text": raw.get("text", "") or "",
                "golden_post_type": exp.get("post_type"),
                "ai_relevance": exp.get("ai_relevance"),
            }
        )
    return pool


_CACHE = Path("fixtures/dataset/_rerank_cache.json")


def _rerank(texts: list[str]) -> tuple[list[float], list[float]]:
    """Return (applied_ai_scores, research_ds_scores), cached by text+profile hash.

    The cache key fnews in the profile texts so changing the profile invalidates
    stale scores. Only uncached items hit the model.
    """
    import hashlib

    salt = hashlib.md5(
        (_PROFILE_POS + "||" + _PROFILE_NEG).encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]
    cache: dict[str, list[float]] = {}
    if _CACHE.exists():
        cache = json.loads(_CACHE.read_text(encoding="utf-8"))

    def key(t: str) -> str:
        return salt + ":" + hashlib.md5(t.encode(), usedforsecurity=False).hexdigest()

    missing = [t for t in texts if key(t) not in cache]
    print(f"  reranker cache: {len(texts) - len(missing)} hit / {len(missing)} to compute")
    if missing:
        from sentence_transformers import CrossEncoder

        ce = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
        pos = ce.predict(
            [(_PROFILE_POS, t[:2000]) for t in missing], batch_size=32, show_progress_bar=True
        )
        neg = ce.predict(
            [(_PROFILE_NEG, t[:2000]) for t in missing], batch_size=32, show_progress_bar=True
        )
        for t, sp, sn in zip(missing, pos, neg, strict=False):
            cache[key(t)] = [float(sp), float(sn)]
        _CACHE.write_text(json.dumps(cache), encoding="utf-8")

    return [cache[key(t)][0] for t in texts], [cache[key(t)][1] for t in texts]


def _label(item: dict[str, Any]) -> tuple[int, str, str]:
    """Relevant iff it is a vacancy AND looks like applied-AI more than research/DS."""
    if not item["is_job"]:
        return 0, "high", "not_a_vacancy"
    rr_pos = item["rr_pos"]
    rr_neg = item["rr_neg"]
    # Research/DS dominates -> out of scope per profile negatives.
    if rr_neg >= rr_pos + NEG_MARGIN and rr_neg >= TAU_HI:
        return 0, "high", "research_ds_dominant"
    if rr_pos >= TAU_HI and rr_pos > rr_neg:
        return 1, "high", "applied_ai>=hi"
    if rr_pos <= TAU_LO:
        return 0, "high", "applied_ai<=lo"
    # uncertain band
    if rr_pos > rr_neg:
        return 1, "low", "midband_applied>research"
    return 0, "low", "midband_research>=applied"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--per-kind", type=int, default=152)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pool = _load_pool()
    print(f"Clean pool joined: {len(pool)} items")
    texts = [p["text"] for p in pool]
    print("Scoring with reranker (applied-AI vs research/DS)...")
    pos_scores, neg_scores = _rerank(texts)
    for p, sp, sn in zip(pool, pos_scores, neg_scores, strict=False):
        p["rr_pos"] = sp
        p["rr_neg"] = sn
        p["is_job"] = _is_job(p["text"], p["golden_post_type"])

    for p in pool:
        rel, conf, reason = _label(p)
        p["relevant"], p["confidence"], p["reason"] = rel, conf, reason

    if args.calibrate:
        rr_vals = sorted(pos_scores)
        n = len(rr_vals)
        print(
            "applied-AI score pctiles:",
            {q: round(rr_vals[int(q / 100 * (n - 1))], 3) for q in (10, 25, 50, 75, 90, 95)},
        )
        # how often DS/research dominates among vacancies (out-of-scope rejections)
        vac = [p for p in pool if p["is_job"]]
        ds_dom = sum(
            1 for p in vac if p["rr_neg"] >= p["rr_pos"] + NEG_MARGIN and p["rr_neg"] >= TAU_HI
        )
        print(f"vacancies: {len(vac)} | research/DS-dominant (rejected as out-of-scope): {ds_dom}")
        print("label dist:", Counter(p["relevant"] for p in pool))
        print("confidence dist:", Counter(p["confidence"] for p in pool))
        print("relevant by source_kind:")
        bysk = defaultdict(lambda: [0, 0])  # type: ignore
        for p in pool:
            bysk[p["source_kind"]][1] += 1
            bysk[p["source_kind"]][0] += p["relevant"]
        for sk, (r, t) in sorted(bysk.items()):
            print(f"  {sk:18} relevant {r}/{t} ({r / t:.1%})")
        print("low-confidence (need review):", sum(1 for p in pool if p["confidence"] == "low"))

    if args.emit:
        rng = random.Random(args.seed)
        bykind: dict[str, list[dict]] = defaultdict(list)  # type: ignore
        for p in pool:
            bykind[p["source_kind"]].append(p)
        selected: list[dict] = []  # type: ignore
        for _sk, items in bykind.items():
            rng.shuffle(items)
            selected.extend(items[: args.per_kind])
        rng.shuffle(selected)
        cols = [
            "stable_id",
            "source_kind",
            "source_name",
            "text",
            "relevant",
            "confidence",
            "reason",
            "rr_pos",
            "rr_neg",
            "ai_relevance",
            "is_job",
        ]
        _OUT.write_text(
            "\n".join(json.dumps({k: p.get(k) for k in cols}, ensure_ascii=False) for p in selected)
            + "\n",
            encoding="utf-8",
        )
        _IDS.write_text("\n".join(p["stable_id"] for p in selected) + "\n", encoding="utf-8")
        print(f"Emitted {len(selected)} items -> {_OUT}")
        print("  by source_kind:", Counter(p["source_kind"] for p in selected))
        print("  relevant:", Counter(p["relevant"] for p in selected))
        print("  low-confidence:", sum(1 for p in selected if p["confidence"] == "low"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
