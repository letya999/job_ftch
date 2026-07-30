"""Embed the curated profile shots and store them in the Qdrant DB.

Reads ``fixtures/dataset/profile_shots.jsonl`` (seed example postings, labelled
positive/negative for the active AI-engineering / vibecoder / AI-automation
profile), embeds each with the local multilingual model, and upserts the vectors
into the ``profile_shots_e5`` Qdrant collection. This is the DB-backed profile
that replaces YAML keyword lists for relevance.

Usage:
    python scripts/eval/seed_shots.py
"""

from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from job_ftch.infrastructure.relevance.shot_anchor import Shot, ShotEmbedder, ShotStore

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")

_SHOTS = Path("fixtures/dataset/profile_shots.jsonl")


def main() -> int:
    rows = [
        json.loads(line) for line in _SHOTS.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    by_label = Counter(r["label"] for r in rows)
    print(f"Loaded {len(rows)} shots: {dict(by_label)}")

    embedder = ShotEmbedder()
    vectors = embedder.encode_passages([r["text"] for r in rows])
    shots = [
        Shot(label=r["label"], role=r.get("role", ""), text=r["text"], vector=vectors[i])
        for i, r in enumerate(rows)
    ]

    store = ShotStore(
        url=os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333"),
        api_key=os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None,
    )
    store.recreate()
    n = store.upsert_shots(shots)
    print(
        f"Upserted {n} shot vectors (dim={embedder.dim}) into Qdrant collection 'profile_shots_e5'"
    )

    # Sanity: reload and score two probe texts
    pos, neg = store.load()
    from job_ftch.infrastructure.relevance.shot_anchor import ShotRelevanceScorer

    scorer = ShotRelevanceScorer(pos, neg, embedder)
    for probe in [
        "Вакансия: Data Scientist, машинное обучение, Python, нейросети, опыт от 3 лет",
        "Требуется бухгалтер с опытом работы в 1С, налоговая отчётность",
    ]:
        s = scorer.score_text(probe)
        print(
            f"  probe margin={s.margin:+.3f} (pos={s.sim_pos:.3f} neg={s.sim_neg:.3f}) :: {probe[:50]}"
        )
    print(f"Store loaded: {pos.shape[0]} positive, {neg.shape[0]} negative vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
