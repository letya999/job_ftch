"""Quick: does the LLM (with shot-grounded prompt) actually separate gold pos/neg?

Tests two prompt styles x the configured model on 10 gold-relevant + 10 gold-not
real items. Cheap (~40 calls). Shows raw answers + accuracy so we know whether the
band-LLM failure was the prompt, the model, or the thresholds.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DATASET = ROOT / "fixtures" / "dataset" / "eval_dataset.jsonl"
SHOTS = ROOT / "fixtures" / "shots" / "all_shots.jsonl"
MODEL = os.environ.get("JOB_FTCH_OPENAI_MODEL", "gpt-4.1-nano")


def main() -> None:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["JOB_FTCH_OPENAI_API_KEY"])

    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    jobs = [r for r in rows if r.get("is_job") == 1]
    rel = [r for r in jobs if r.get("relevant") == 1]
    irr = [r for r in jobs if r.get("relevant") == 0]
    rng = random.Random(7)
    rng.shuffle(rel)
    rng.shuffle(irr)
    test = [(r, 1) for r in rel[:10]] + [(r, 0) for r in irr[:10]]

    with SHOTS.open(encoding="utf-8") as handle:
        shots = [json.loads(line) for line in handle if line.strip()]
    vpos = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "positive"][:3]
    vneg = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "negative"][:3]

    # Style B: concise, balanced, asks for reasoning then verdict.
    sys_b = (
        "You filter job postings for a candidate who builds PRODUCTS using LLM/AI APIs "
        "(LLM apps, AI agents, RAG, AI automation, GenAI, prompt engineering, AI-first "
        "fullstack). Accept if the role's core is APPLYING AI/LLM to build things. Reject "
        "if the core is data science, classic/research ML, training/fine-tuning models, "
        "MLOps, computer vision, data engineering, analytics, or management."
    )

    def ex_block(pos, neg):  # type: ignore
        return "\n\n".join(
            [f"WANT example:\n{t[:600]}" for t in pos]
            + [f"REJECT example:\n{t[:600]}" for t in neg]
        )

    def ask(style, text):  # type: ignore
        if style == "A":  # original strict
            sysp = (
                "You are a relevance filter. Accept ONLY roles whose CORE is building "
                "products that CALL LLM/AI APIs. Reject DS, ML, research, training, MLOps, "
                "CV, data eng, analytics, PM - even if they mention LLM. Answer one word."
            )
            usr = f"{ex_block(vpos, vneg)}\n\nJOB:\n{text[:2200]}\n\naccept or reject?"  # type: ignore
            mt = 5
        else:  # B reasoning
            sysp = sys_b
            usr = (
                f"{ex_block(vpos, vneg)}\n\nJOB TO JUDGE:\n{text[:2200]}\n\n"  # type: ignore
                "Is the CORE of this role applying AI/LLM to build products? "
                "Reply exactly 'accept' or 'reject'."
            )
            mt = 5
        r = client.chat.completions.create(
            model=MODEL,
            temperature=0,
            max_tokens=mt,
            messages=[{"role": "system", "content": sysp}, {"role": "user", "content": usr}],
        )
        return (r.choices[0].message.content or "").strip().lower()

    for style in ("A", "B"):
        tp = fp = fn = tn = 0
        print(f"\n=== style {style}, model={MODEL} ===")
        for r, g in test:
            ans = ask(style, r.get("text") or "")  # type: ignore
            acc = 1 if "accept" in ans else 0
            tp += acc == 1 and g == 1
            fp += acc == 1 and g == 0
            fn += acc == 0 and g == 1
            tn += acc == 0 and g == 0
            mark = "OK" if acc == g else "XX"
            print(
                f"  {mark} gold={g} ans={ans[:12]:12} | {r.get('source_name')} {(r.get('text') or '')[:40]}"
            )
        p = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        print(f"  -> P={p:.2f} R={rc:.2f} (TP={tp} FP={fp} FN={fn} TN={tn})")


if __name__ == "__main__":
    main()
