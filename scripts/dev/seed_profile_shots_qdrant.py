from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from job_ftch.config import get_settings
from job_ftch.domain.bgem3_card import build_bgem3_card
from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeQdrantShotStore, Shot

DEFAULT_SHOTS_FILE = Path("fixtures/shots/all_shots.jsonl")
DEFAULT_TENANT_ID = "ai_jobs"
DEFAULT_USER_ID = "480637186"
DEFAULT_PROFILE_ID = "user_480637186"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed profile shots into Qdrant from JSONL.")
    parser.add_argument("--shots-file", type=Path, default=DEFAULT_SHOTS_FILE)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--profile-id", default=DEFAULT_PROFILE_ID)
    return parser.parse_args()


def _qdrant_point_id(*parts: str) -> str:
    import hashlib
    import uuid

    digest = hashlib.md5("\u241f".join(parts).encode(), usedforsecurity=False).hexdigest()
    return str(uuid.UUID(digest))


def _load_rows(path: Path) -> list[dict[str, str]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if len(rows) != 40:
        raise ValueError(f"Expected 40 shots in {path}, found {len(rows)}")
    return rows


def main() -> int:
    args = parse_args()
    settings = get_settings()
    from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider

    rows = _load_rows(args.shots_file)
    provider = BgeMThreeProvider(settings.bgem3_model)
    store = BgeMThreeQdrantShotStore(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        collection=settings.relevance_shot_collection_bgem3,
        provider=provider,
    )
    store.ensure_collection()

    upserts: list[tuple[str, Shot]] = []
    for row in rows:
        source_type = "resume" if row["kind"] == "resume" else "vacancy"
        category = f"{source_type}:{row['label']}"
        full_role = f"user:{args.user_id}@tenant:{args.tenant_id}:{category}"
        encoded = provider.encode(
            build_bgem3_card(str(row["text"]), max_chars=4096),
            max_length=1024,
            return_sparse=True,
        )
        upserts.append(
            (
                _qdrant_point_id(
                    args.tenant_id,
                    args.user_id,
                    args.profile_id,
                    category,
                    str(row["id"]),
                ),
                Shot(
                    label=str(row["label"]),
                    role=full_role,
                    text=str(row["text"]),
                    vector=np.asarray(encoded["dense"], dtype=np.float32),
                    sparse_weights={
                        str(int(key)): float(value)
                        for key, value in (encoded.get("sparse") or {}).items()
                    },
                    provenance={
                        "tenant_id": args.tenant_id,
                        "user_id": args.user_id,
                        "profile_id": args.profile_id,
                        "category": category,
                        "source_type": source_type,
                        "embedding_model": settings.bgem3_model,
                        "embedding_version": "pdf_refresh_v1",
                    },
                ),
            )
        )

    written = store.upsert_shots_with_ids(upserts)
    print(
        json.dumps(
            {
                "tenant_id": args.tenant_id,
                "user_id": args.user_id,
                "profile_id": args.profile_id,
                "collection": settings.relevance_shot_collection_bgem3,
                "qdrant_written": written,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
