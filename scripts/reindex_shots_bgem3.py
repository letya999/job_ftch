"""Reindex profile shots into Qdrant using BGE-M3 dense+sparse vectors.

Usage:
    uv run --extra bgem3 --extra qdrant python scripts/reindex_shots_bgem3.py

Reads from: config/profiles/shots.yaml  (11 positives + 12 negatives)
Writes to:  Qdrant collection 'profile_shots_bgem3' (1024-dim cosine)

Set JOB_FTCH_QDRANT_URL (and optionally JOB_FTCH_QDRANT_API_KEY) in your .env
or export them before running.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    shots_path = ROOT / "profiles" / "shots.yaml"
    if not shots_path.exists():
        print(f"ERROR: {shots_path} not found", file=sys.stderr)
        sys.exit(1)

    qdrant_url = os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
    collection = os.environ.get("JOB_FTCH_RELEVANCE_SHOT_COLLECTION_BGEM3", "profile_shots_bgem3")

    with shots_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    shots_raw: list[dict] = []  # type: ignore
    for entry in data.get("positives", []):
        shots_raw.append(
            {
                "label": "positive",
                "role": entry.get("role", ""),
                "text": entry["text"].strip(),
            }
        )
    for entry in data.get("negatives", []):
        shots_raw.append(
            {
                "label": "negative",
                "role": entry.get("role", ""),
                "text": entry["text"].strip(),
            }
        )

    n_pos = sum(1 for s in shots_raw if s["label"] == "positive")
    n_neg = sum(1 for s in shots_raw if s["label"] == "negative")
    print(f"Loaded {len(shots_raw)} shots (pos={n_pos}, neg={n_neg}) from {shots_path}")

    texts = [s["text"] for s in shots_raw]

    print("Loading BAAI/bge-m3 (CPU, use_fp16=False) ...")
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    print("Encoding shots ...")
    out = model.encode(
        texts,
        batch_size=4,
        max_length=512,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense_vecs: list[np.ndarray] = [
        np.asarray(out["dense_vecs"][i], dtype=np.float32) for i in range(len(texts))
    ]
    sparse_vecs: list[dict] = out.get("lexical_weights", [{} for _ in texts])  # type: ignore
    print(f"Encoded {len(dense_vecs)} vectors, dim={dense_vecs[0].shape[0]}")

    from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeShotStore, Shot

    store = BgeMThreeShotStore(url=qdrant_url, api_key=qdrant_api_key, collection=collection)
    store.recreate()
    print(f"Recreated Qdrant collection '{collection}'")

    shots_to_upsert = [
        Shot(
            label=s["label"],
            role=s.get("role", ""),
            text=s["text"],
            vector=vec,
            sparse_weights={str(k): float(v) for k, v in sp_vec.items()} if sp_vec else None,
        )
        for s, vec, sp_vec in zip(shots_raw, dense_vecs, sparse_vecs, strict=False)
    ]
    n = store.upsert_shots(shots_to_upsert)
    print(f"Upserted {n} shots to '{collection}'")
    print("Done.")


if __name__ == "__main__":
    main()
