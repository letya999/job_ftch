"""Quick debug: check parallel scoring values for 5 items."""

import asyncio
import json
import os

os.environ["JOB_FTCH_BGEM3_ENABLED"] = "true"
os.environ["JOB_FTCH_RELEVANCE_BACKEND"] = "shots"

import numpy as np

from job_ftch.config import get_settings
from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider
from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeShotStore


async def main() -> None:
    settings = get_settings()
    provider = BgeMThreeProvider(settings.bgem3_model)

    store = BgeMThreeShotStore(
        url=str(settings.qdrant_url),
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
        ),
        collection=settings.relevance_shot_collection_bgem3,
    )
    pos_dense, neg_dense, pos_sparse, neg_sparse = store.load()
    print(
        f"Shots: pos={pos_dense.shape} neg={neg_dense.shape} pos_sparse_nonempty={sum(bool(s) for s in pos_sparse)}"
    )

    # Check vector norms
    norms = np.linalg.norm(pos_dense, axis=1)
    print(f"Pos shot norms: min={norms.min():.3f} max={norms.max():.3f} mean={norms.mean():.3f}")

    # Encode 3 items and compute scores
    items = []
    with open("fixtures/dataset/eval_dataset.jsonl") as f:
        for line in f:
            items.append(json.loads(line))
            if len(items) >= 5:
                break

    for d in items:
        text = d.get("text", "")[:512]
        if not text:
            continue
        result = provider.encode(text)
        vec = result["dense"]
        norm = float(np.linalg.norm(vec))
        pos_sims = pos_dense @ vec
        neg_sims = neg_dense @ vec
        print(
            f"gold={d.get('gold_label', '?')} "
            f"vec_norm={norm:.3f} "
            f"max_pos_sim={pos_sims.max():.4f} "
            f"mean_pos_sim={pos_sims.mean():.4f} "
            f"max_neg_sim={neg_sims.max():.4f}"
        )


asyncio.run(main())
