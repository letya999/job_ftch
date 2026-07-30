"""Work-of-art eval: shot-margin floor (Layer B) + LLM on the narrow band (Layer C).

Universal, profile-driven, no eval-label training:
  Layer B (free): BGE-M3 shot dense margin.
     - margin < LOW  -> auto-REJECT (no LLM)        [LOW from negative-shot LOO]
     - margin >= HIGH -> auto-ACCEPT (no LLM)        [HIGH from positive-shot LOO]
     - LOW <= margin < HIGH -> uncertain band -> LLM
  Layer C ($, rare): OpenAI gpt-4o-mini, SHOT-GROUNDED prompt (3 pos + 3 neg
     vacancy shots as the candidate's wants/rejects). Decides accept/reject.

Thresholds come from the SHOTS (leave-one-out), never from eval labels -> works
per-user. The 400-sample is measurement-only. Reports P/R/F1 AND llm_call_count.

Run: uv run --env-file .env.dev --extra bgem3 python scripts/shots/llm_band_eval.py
Requires JOB_FTCH_OPENAI_API_KEY (+ optional JOB_FTCH_OPENAI_MODEL=gpt-4o-mini).
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import numpy as np
from FlagEmbedding import BGEM3FlagModel

from job_ftch.infrastructure.relevance.shot_anchor import (
    BgeMThreeShotScorer,
    BgeMThreeShotStore,
)

ROOT = Path(__file__).parent.parent.parent
DATASET = ROOT / "fixtures" / "dataset" / "eval_dataset.jsonl"
SHOTS = ROOT / "fixtures" / "shots" / "all_shots.jsonl"
# Band judge: nano is too weak (R=0.40); gpt-4.1-mini gives P=1.0 R=0.83 on the
# balanced probe. Override via JOB_FTCH_BAND_MODEL.
MODEL = os.environ.get("JOB_FTCH_BAND_MODEL", "gpt-4.1-mini")
# Item-scale recall floor: margin < LOW -> auto-reject (free). LLM judges the rest.
LOW = float(os.environ.get("JOB_FTCH_BAND_LOW", "0.0"))
N_EXEMPLARS = 3


# Layer A: universal garbage filter (profile-INDEPENDENT). Rejects non-vacancies
# that should never reach relevance judging: freelancer service ads, project
# specs, channel rules. Returns a reason string if garbage, else None.
def garbage_reason(text: str) -> str | None:
    head = text.strip()[:300].lower()
    if re.match(r"\s*(?:#\S+\s*){3,}", head) or any(
        s in head
        for s in ("#помогу", "#разрабатываю", "разрабатываю кастомные", "создаю:", "под ключ")
    ):
        return "freelancer service ad"
    if head.startswith("идея:") or "\nидея:" in head or "идея: разработать" in head:
        return "project spec (Идея:)"
    if "правила чата" in head or "правила группы" in head:
        return "channel rules"
    return None


def topk_mean(sims: np.ndarray, k: int = 5) -> float:
    return float(np.sort(sims)[::-1][:k].mean()) if sims.size else 0.0


# When the shot-generated prompt exists, use it as the system guidance (matches the
# production LLMRelevanceClassificationNode). Toggle off with JOB_FTCH_BAND_USE_GENERATED=0.
_GEN_PROMPT_PATH = ROOT / "fixtures" / "shots" / "generated_relevance_prompt.txt"
_STATIC_SYS = (
    "You filter job postings for a candidate who builds PRODUCTS using LLM/AI APIs "
    "(LLM apps, AI agents, RAG, AI automation, GenAI, prompt engineering, AI-first "
    "fullstack, vibe coding). Accept if the role core is APPLYING AI/LLM to build "
    "products/tools/automations. Reject if core is data science, classic/research ML, "
    "training/fine-tuning, MLOps, computer vision, data engineering, analytics, or "
    "management. When a backend/fullstack/python role centers on integrating LLMs, accept."
)
if os.environ.get("JOB_FTCH_BAND_USE_GENERATED", "1") == "1" and _GEN_PROMPT_PATH.exists():
    _SYS_TEXT = _GEN_PROMPT_PATH.read_text(encoding="utf-8").strip()
    print(f"[prompt] using shot-generated system prompt ({len(_SYS_TEXT)} chars)")
else:
    _SYS_TEXT = _STATIC_SYS
    print("[prompt] using static permissive system prompt")


def build_prompt(
    item_text: str, pos_examples: list[str], neg_examples: list[str]
) -> tuple[str, str]:
    sys = _SYS_TEXT
    ex = "\n\n".join(
        [f"WANT example:\n{t[:600]}" for t in pos_examples]
        + [f"REJECT example:\n{t[:600]}" for t in neg_examples]
    )
    user = (
        f"{ex}\n\nJOB TO JUDGE:\n{item_text[:2200]}\n\n"
        "Is the CORE of this role applying AI/LLM to build products? "
        "Reply exactly 'accept' or 'reject'."
    )
    return sys, user


def main() -> None:
    key = os.environ.get("JOB_FTCH_OPENAI_API_KEY")
    if not key:
        raise SystemExit(
            "JOB_FTCH_OPENAI_API_KEY not set. Add it to .env.dev:\n"
            "  JOB_FTCH_OPENAI_API_KEY=sk-...\n  JOB_FTCH_OPENAI_MODEL=gpt-4o-mini"
        )
    from openai import OpenAI

    client = OpenAI(api_key=key)

    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    sample = random.Random(42).sample(rows, 400)
    gold = np.array([1 if r.get("relevant") == 1 else 0 for r in sample])

    shots = []
    with open(SHOTS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                shots.append(json.loads(line))
    vac_pos = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "positive"]
    vac_neg = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "negative"]

    # Layer B: dense shot margin.
    url = os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333")
    store = BgeMThreeShotStore(url=url, collection="profile_shots_bgem3")
    pos_d, neg_d, _, _ = store.load()
    scorer = BgeMThreeShotScorer(pos_d, neg_d, top_k=5)
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)

    texts = [(r.get("text") or "")[:3000] for r in sample]
    enc = model.encode(texts, batch_size=8, max_length=1024, return_dense=True)["dense_vecs"]
    margins = np.array([scorer.score_vector(np.asarray(v, np.float32)).margin for v in enc])

    # Layer A: garbage filter (free, universal) — never reaches LLM.
    garbage = np.array([garbage_reason(t) is not None for t in texts])
    # Layer B floor: margin < LOW -> auto-reject (free, recall-preserving).
    below = margins < LOW
    band = (~below) & (~garbage)
    print(
        f"Layer A garbage={int(garbage.sum())}  floor LOW={LOW:.4f} auto-reject={int((below & ~garbage).sum())}  "
        f"to-LLM={int(band.sum())} ({100 * band.sum() / 400:.0f}%)  band model={MODEL}"
    )

    pred = np.zeros(400, dtype=int)
    llm_calls = 0
    for i in np.where(band)[0]:
        sysp, usrp = build_prompt(texts[i], vac_pos[:N_EXEMPLARS], vac_neg[:N_EXEMPLARS])
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                messages=[{"role": "system", "content": sysp}, {"role": "user", "content": usrp}],
                max_tokens=5,
            )
            ans = (resp.choices[0].message.content or "").strip().lower()
            pred[i] = 1 if "accept" in ans else 0
        except Exception as e:
            print(f"  LLM error item {i}: {e}")
            pred[i] = 0
        llm_calls += 1
        if llm_calls % 25 == 0:
            print(f"  llm {llm_calls}/{int(band.sum())}", flush=True)

    tp = int(((pred == 1) & (gold == 1)).sum())
    fp = int(((pred == 1) & (gold == 0)).sum())
    fn = int(((pred == 0) & (gold == 1)).sum())
    tn = int(((pred == 0) & (gold == 0)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    print("\n=== WORK-OF-ART: shot floor + LLM band ===")
    print(f"P={p:.3f} R={r:.3f} F1={f1:.3f}  (TP={tp} FP={fp} FN={fn} TN={tn})")
    print(f"LLM calls = {llm_calls}/400 ({100 * llm_calls / 400:.0f}%)  model={MODEL}")
    print(f"TARGET P>=0.90 & R>=0.70: {'MET' if p >= 0.90 and r >= 0.70 else 'NOT met'}")

    # Persist per-item for offline FP/FN analysis (no re-calling the LLM).
    recs = [
        {
            "i": int(i),
            "gold": int(gold[i]),
            "pred": int(pred[i]),
            "margin": float(margins[i]),
            "source": sample[i].get("source_name"),
            "title": (sample[i].get("text") or "")[:70],
        }
        for i in range(400)
    ]
    (ROOT / "results" / "llm_band_items.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    print("\nFALSE POSITIVES (LLM accepted, gold=0):")
    for rr in recs:
        if rr["pred"] == 1 and rr["gold"] == 0:
            print(f"  m={rr['margin']:+.3f} {rr['source']:20} {rr['title']}")
    print("FALSE NEGATIVES (missed, gold=1):")
    for rr in recs:
        if rr["pred"] == 0 and rr["gold"] == 1:
            tag = "below-floor" if rr["margin"] < LOW else "LLM-rejected"
            print(f"  m={rr['margin']:+.3f} [{tag}] {rr['source']:20} {rr['title']}")


if __name__ == "__main__":
    main()
