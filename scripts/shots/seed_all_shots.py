"""Seed the 40 trustworthy shots (fixtures/shots/all_shots.jsonl) into Qdrant.

Replaces the new YAML-sourced shots in collection 'profile_shots_bgem3' with
the new 40-shot set: 10 pos resume + 10 neg resume + 10 pos vacancy + 10 neg
vacancy. Encodes dense+sparse with BGE-M3.

Usage:
    uv run --env-file .env.dev --extra bgem3 python scripts/shots/seed_all_shots.py

The label field (positive/negative) drives the relevance margin. kind
(resume/vacancy) is kept in payload for diagnostics and future per-kind scoring.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

SHOTS_FILE = ROOT / "fixtures" / "shots" / "all_shots.jsonl"


def main() -> None:
    if not SHOTS_FILE.exists():
        print(f"ERROR: {SHOTS_FILE} not found", file=sys.stderr)
        sys.exit(1)

    shots_raw: list[dict] = []  # type: ignore
    with SHOTS_FILE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                shots_raw.append(json.loads(line))

    n_pos = sum(1 for s in shots_raw if s["label"] == "positive")
    n_neg = sum(1 for s in shots_raw if s["label"] == "negative")
    by_kind = {}  # type: ignore
    for s in shots_raw:
        by_kind[(s["kind"], s["label"])] = by_kind.get((s["kind"], s["label"]), 0) + 1
    print(f"Loaded {len(shots_raw)} shots (pos={n_pos}, neg={n_neg})")
    for k, v in sorted(by_kind.items()):
        print(f"  {k[0]}/{k[1]}: {v}")

    # Hard requirement: at least 10 of each of the 4 types.
    for kind in ("resume", "vacancy"):
        for label in ("positive", "negative"):
            cnt = by_kind.get((kind, label), 0)
            if cnt < 10:
                print(f"ERROR: need >=10 {kind}/{label} shots, have {cnt}", file=sys.stderr)
                sys.exit(2)

    qdrant_url = os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
    collection = os.environ.get("JOB_FTCH_RELEVANCE_SHOT_COLLECTION_BGEM3", "profile_shots_bgem3")
    tenant_id = os.environ.get("JOB_FTCH_TENANT_ID", "ai_jobs")

    texts = [s["text"] for s in shots_raw]

    print("Loading BAAI/bge-m3 (CPU, use_fp16=False) ...")
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    print("Encoding shots ...")
    out = model.encode(
        texts,
        batch_size=4,
        max_length=1024,  # resumes are long (avg ~3000 chars); avoid truncation
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense_vecs = [np.asarray(out["dense_vecs"][i], dtype=np.float32) for i in range(len(texts))]
    sparse_vecs = out.get("lexical_weights", [{} for _ in texts])
    print(f"Encoded {len(dense_vecs)} vectors, dim={dense_vecs[0].shape[0]}")

    from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeShotStore, Shot

    store = BgeMThreeShotStore(url=qdrant_url, api_key=qdrant_api_key, collection=collection)
    store.ensure_collection()
    store.clear()
    print(f"Recreated Qdrant collection '{collection}'")

    shots_to_upsert = [
        Shot(
            label=s["label"],
            role=f"{s['kind']}:{s.get('role', '')}@tenant:{tenant_id}:",
            text=s["text"],
            vector=vec,
            sparse_weights={str(k): float(v) for k, v in sp_vec.items()} if sp_vec else None,
        )
        for s, vec, sp_vec in zip(shots_raw, dense_vecs, sparse_vecs, strict=False)
    ]
    n = store.upsert_shots(shots_to_upsert)
    print(f"Upserted {n} shots to '{collection}'. Done.")


if __name__ == "__main__":
    main()
