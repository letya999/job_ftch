"""Extract keywords locally from shots and discriminative tokens."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

SHOTS_FILE = ROOT / "fixtures" / "shots" / "all_shots.jsonl"
OUT_FILE = ROOT / "fixtures" / "shots" / "derived_keywords.json"
_BGE_M3_MODEL = "BAAI/bge-m3"
# Pinned to the current verified commit from Hugging Face commit history:
# https://huggingface.co/BAAI/bge-m3/commits/main
_BGE_M3_REVISION = "5617a9f"


def main() -> None:
    if not SHOTS_FILE.exists():
        print(f"ERROR: {SHOTS_FILE} not found", file=sys.stderr)
        sys.exit(1)

    shots_raw = []
    with SHOTS_FILE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                shots_raw.append(json.loads(line))

    roles = set()
    skills = set()
    anti_patterns = set()

    for s in shots_raw:
        kind = s.get("kind")
        label = s.get("label")
        text = s.get("text", "")
        role = s.get("role", "")

        if kind == "resume":
            if label == "positive":
                # Parse explicit sections
                for line in text.splitlines():
                    if line.startswith("Интересующие роли:"):
                        roles.update(
                            [
                                r.strip()
                                for r in line.replace("Интересующие роли:", "").split(",")
                                if r.strip()
                            ]
                        )
                    elif line.startswith("Ключевые навыки:"):
                        skills.update(
                            [
                                sk.strip()
                                for sk in line.replace("Ключевые навыки:", "").split(",")
                                if sk.strip()
                            ]
                        )
                    elif line.startswith("Не основной фокус:"):
                        anti_patterns.update(
                            [
                                a.strip()
                                for a in line.replace("Не основной фокус:", "").split(",")
                                if a.strip()
                            ]
                        )
            elif label == "negative" and role:
                anti_patterns.add(role)
        elif kind == "vacancy":
            if label == "positive" and role:
                roles.add(role)
            elif label == "negative" and role:
                anti_patterns.add(role)

    print("Loading BGE-M3 store...")
    qdrant_url = os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
    collection = os.environ.get("JOB_FTCH_RELEVANCE_SHOT_COLLECTION_BGEM3", "profile_shots_bgem3")

    from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeShotStore

    store = BgeMThreeShotStore(url=qdrant_url, api_key=qdrant_api_key, collection=collection)

    pos_d, neg_d, pos_s, neg_s = store.load()

    disc: dict[int, float] = {}
    for s in pos_s:
        for k, v in s.items():
            disc[k] = disc.get(k, 0.0) + v
    for s in neg_s:
        for k, v in s.items():
            disc[k] = disc.get(k, 0.0) - v

    # Sort
    sorted_tokens = sorted(disc.items(), key=lambda x: x[1], reverse=True)
    top_pos_ids = [k for k, v in sorted_tokens[:50] if v > 0]
    top_neg_ids = [k for k, v in sorted_tokens[-50:] if v < 0]

    print("Loading tokenizer...")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(_BGE_M3_MODEL, revision=_BGE_M3_REVISION)

    pos_tokens = tokenizer.convert_ids_to_tokens(top_pos_ids)
    neg_tokens = tokenizer.convert_ids_to_tokens(top_neg_ids)

    # Clean up token strings
    def clean_tokens(tokens: str | list[str]) -> list[str]:
        token_list = [tokens] if isinstance(tokens, str) else tokens
        return [t.replace(" ", "").replace(" ", "") for t in token_list if len(t.strip()) > 1]

    out_data = {
        "roles": sorted(list(roles)),
        "skills": sorted(list(skills)),
        "anti_patterns": sorted(list(anti_patterns)),
        "positive_tokens": clean_tokens(pos_tokens),
        "negative_tokens": clean_tokens(neg_tokens),
    }

    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
