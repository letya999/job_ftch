"""Label the gold eval set with a cheap LLM judge (gpt-4o-mini).

Replaces the slow local cross-encoder. For each posting in the frozen 456-item
sample, ask the LLM: is this a real job vacancy, and does it match the user's
ACTIVE profile - applied AI engineering (LLM/AI integration, agent & workflow
automation, vibe coding, fullstack AI, ML/AI platform) - and NOT pure data
science / ML research (which the user marked as out of scope).

Output: fixtures/dataset/gold_eval.jsonl with a binary ``relevant`` label.

Usage:
    python scripts/eval/label_llm.py
"""

from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")

_CLEAN = Path("fixtures/dataset/clean_pool.jsonl")
_IDS = Path("fixtures/dataset/gold_eval_ids.txt")
_OUT = Path("fixtures/dataset/gold_eval.jsonl")
_MODEL = "gpt-4o-mini"

_SYSTEM = """You label job-market posts for a candidate's vacancy feed.

The candidate is an APPLIED AI engineer. RELEVANT roles (label relevant=1):
- AI / LLM engineer integrating GPT / LLM APIs into products, RAG, agents
- AI automation engineer (n8n, agent workflows, business process automation)
- AI-assisted / "vibe coding" developer, rapid product prototyping
- fullstack / product engineer building AI features
- ML backend / AI platform / AI infrastructure engineer (applied, shipping)

NOT RELEVANT (label relevant=0):
- pure Data Scientist, ML researcher, NLP/CV research, "train models from scratch",
  statistical modeling / data analyst roles  (explicitly out of scope)
- any non-AI role (generic backend/frontend, sales, QA, devops, finance, HR)
- anything that is NOT an actual job vacancy (chatter, news, digests, events,
  course ads, someone's CV / "ищу работу")

Return STRICT JSON: {"is_job": 0|1, "relevant": 0|1, "reason": "<=12 words"}.
relevant=1 requires is_job=1 AND an applied-AI role per above."""


def _label_one(client: OpenAI, text: str) -> dict[str, Any]:
    try:
        r = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text[:6000]},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        d = json.loads(r.choices[0].message.content or "{}")
        return {
            "relevant": int(bool(d.get("relevant", 0))),
            "is_job": int(bool(d.get("is_job", 0))),
            "reason": str(d.get("reason", ""))[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {"relevant": 0, "is_job": 0, "reason": f"error:{exc}"[:120]}


def main() -> int:
    ids = [x.strip() for x in _IDS.read_text(encoding="utf-8").splitlines() if x.strip()]
    pool = {
        json.loads(line)["stable_id"]: json.loads(line)
        for line in _CLEAN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    items = [pool[i] for i in ids if i in pool]
    print(f"Labeling {len(items)} items with {_MODEL} ...")

    client = OpenAI(
        api_key=os.environ["JOB_FTCH_OPENAI_API_KEY"],
        base_url=os.environ.get("JOB_FTCH_OPENAI_BASE_URL"),
    )

    def work(raw: dict[str, Any]) -> dict[str, Any]:
        lab = _label_one(client, raw.get("text", "") or "")
        return {
            "stable_id": raw.get("stable_id"),
            "source_kind": raw.get("source_kind"),
            "source_name": raw.get("source_name"),
            "text": raw.get("text", ""),
            "relevant": lab["relevant"],
            "is_job": lab["is_job"],
            "reason": lab["reason"],
            "labeler": _MODEL,
        }

    out: list[dict] = []  # type: ignore
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, row in enumerate(ex.map(work, items), 1):
            out.append(row)
            if i % 50 == 0 or i == len(items):
                print(f"  {i}/{len(items)} labeled")

    _OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {_OUT}")
    print("  relevant:", Counter(r["relevant"] for r in out))
    print("  by source_kind:")
    by: dict[str, list[int]] = {}
    for r in out:
        by.setdefault(r["source_kind"], []).append(r["relevant"])
    for sk, vals in sorted(by.items()):
        print(f"    {sk:18} relevant {sum(vals)}/{len(vals)} ({sum(vals) / len(vals):.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
