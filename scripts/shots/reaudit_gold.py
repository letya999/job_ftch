"""
Re-audit gold labels for the 400-sample eval using a stronger LLM pass.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).parent.parent.parent
DATASET = ROOT / "fixtures" / "dataset" / "eval_dataset.jsonl"
SHOTS = ROOT / "fixtures" / "shots" / "all_shots.jsonl"
OUT_FILE = ROOT / "fixtures" / "dataset" / "eval_400_reaudit.jsonl"

MODEL = os.environ.get("JOB_FTCH_REAUDIT_MODEL", "gpt-4.1-mini")
N_EXEMPLARS = 3


def build_prompt(
    item_text: str, pos_examples: list[str], neg_examples: list[str]
) -> tuple[str, str]:
    sys_prompt = (
        "You evaluate job postings. Your task is to output a JSON object with two fields:\n"
        "1) 'justification': a one-line explanation of your decision\n"
        "2) 'verdict': exactly 'relevant' or 'not_relevant'\n\n"
        "RUBRIC:\n"
        "- ACCEPT (relevant): role core is APPLYING LLM/AI APIs to build products/agents/RAG/automation, "
        "AI-first fullstack/backend that integrates LLMs, prompt engineering, vibe coding.\n"
        "- REJECT (not_relevant): core is data science, classic/research ML, model training/fine-tuning, MLOps, "
        "computer vision, data engineering, analytics, management, or NON-vacancy text "
        "(blog posts, digests, channel rules, freelancer ads).\n"
        "- Judge by the RESPONSIBILITIES, not by AI buzzwords in the team name."
    )
    ex = "\n\n".join(
        [f"WANT example:\n{t[:600]}" for t in pos_examples]
        + [f"REJECT example:\n{t[:600]}" for t in neg_examples]
    )
    user_prompt = (
        f"EXAMPLES:\n{ex}\n\n"
        f"JOB TO JUDGE:\n{item_text[:2200]}\n\n"
        "Evaluate the job and output the JSON response."
    )
    return sys_prompt, user_prompt


def call_model(client: OpenAI, sys_prompt: str, user_prompt: str, temp: float) -> tuple[int, str]:
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=temp,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        verdict = data.get("verdict", "").strip().lower()
        justification = data.get("justification", "").strip()
        label = 1 if verdict == "relevant" else 0
        return label, justification
    except Exception as exc:
        return 0, f"error: {exc}"


def main() -> None:
    key = os.environ.get("JOB_FTCH_OPENAI_API_KEY")
    if not key:
        raise SystemExit("JOB_FTCH_OPENAI_API_KEY not set.")
    client = OpenAI(api_key=key)

    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    sample = random.Random(42).sample(rows, 400)

    shots = []
    with open(SHOTS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                shots.append(json.loads(line))

    vac_pos = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "positive"]
    vac_neg = [s["text"] for s in shots if s["kind"] == "vacancy" and s["label"] == "negative"]

    pos_examples = vac_pos[:N_EXEMPLARS]
    neg_examples = vac_neg[:N_EXEMPLARS]

    out_records = []
    flips_0_to_1 = 0
    flips_1_to_0 = 0
    agreements = 0

    flip_details = []

    for idx, item in enumerate(sample):
        text = item.get("text", "")
        old_label = 1 if item.get("relevant") == 1 else 0
        new_label = old_label

        sys_prompt, user_prompt = build_prompt(text, pos_examples, neg_examples)

        l1, j1 = call_model(client, sys_prompt, user_prompt, 0.0)
        l2, j2 = call_model(client, sys_prompt, user_prompt, 0.3)

        if l1 == l2:
            new_label = l1
            justification = j1
            conf = "high"
        else:
            l3, j3 = call_model(client, sys_prompt, user_prompt, 0.0)
            new_label = l1 if l1 == l3 else l2
            justification = j1 if l1 == l3 else j2
            conf = "low"

        flipped = new_label != old_label
        if flipped:
            if old_label == 0 and new_label == 1:
                flips_0_to_1 += 1
            else:
                flips_1_to_0 += 1

            flip_details.append(
                {
                    "old": old_label,
                    "new": new_label,
                    "justification": justification,
                    "title": text[:70].replace("\n", " "),
                }
            )
        else:
            agreements += 1

        stable_id = item.get("stable_id", item.get("id", str(idx)))
        source_name = item.get("source_name", "")

        rec = {
            "stable_id": stable_id,
            "idx": idx,
            "old_label": old_label,
            "new_label": new_label,
            "confidence": conf,
            "justification": justification,
            "flipped": flipped,
            "source_name": source_name,
        }
        out_records.append(rec)

        if (idx + 1) % 10 == 0:
            print(f"Processed {idx + 1}/400", flush=True)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== RE-AUDIT SUMMARY ===")
    print("Total evaluated: 400")
    print(f"Agreement rate: {agreements / 400:.1%}")
    print(f"Flips 0 -> 1: {flips_0_to_1}")
    print(f"Flips 1 -> 0: {flips_1_to_0}")
    print("\nFLIPS DETAIL:")
    for fd in flip_details:
        print(f"{fd['new']}->{fd['new']} | {fd['justification']} | {fd['title']}")


if __name__ == "__main__":
    main()
