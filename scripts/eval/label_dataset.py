"""Label the eval dataset with gpt-4o-mini using profile shots from Qdrant."""

from __future__ import annotations

import argparse
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
from qdrant_client import QdrantClient

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")
load_dotenv(".env.dev")

_DEFAULT_DATASET = Path("fixtures/dataset/eval_dataset.jsonl")
# E5 was retired from the active runtime.  Keep the collection explicit so an
# eval labeler cannot silently use an obsolete profile definition.
_DEFAULT_COLLECTION = "profile_shots_bgem3_mvp_v3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Label eval dataset with OpenAI.")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument(
        "--shot-collection",
        default=_DEFAULT_COLLECTION,
        help="Qdrant collection containing positive and negative profile shots.",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"WARN: {path}:{line_no}: invalid JSON: {exc}", file=sys.stderr)
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _scroll_all_points(client: QdrantClient, collection_name: str) -> list[Any]:
    points: list[Any] = []
    offset: Any = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection_name,
            limit=100,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def _load_shots(collection: str) -> tuple[list[str], list[str]]:
    client = QdrantClient(
        url=os.environ.get("JOB_FTCH_QDRANT_URL", "http://localhost:6333"),
        api_key=os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None,
    )
    points = _scroll_all_points(client, collection)
    positive_shots: list[str] = []
    negative_shots: list[str] = []
    for point in points:
        payload = getattr(point, "payload", None) or {}
        text = str(payload.get("text", "") or "").strip()
        label = str(payload.get("label", "") or "").strip().lower()
        if not text:
            continue
        if label == "positive":
            positive_shots.append(text)
        elif label == "negative":
            negative_shots.append(text)
    if not positive_shots or not negative_shots:
        msg = f"{collection} must contain both positive and negative shots."
        raise RuntimeError(msg)
    return positive_shots, negative_shots


def _build_system_prompt(positive_shots: list[str], negative_shots: list[str]) -> str:
    positive_block = "\n---\n".join(positive_shots)
    negative_block = "\n---\n".join(negative_shots)
    return (
        "You label job-market posts for a specific candidate.\n\n"
        "RELEVANT roles for this candidate (label relevant=1):\n"
        "AI / LLM engineer, AI automation engineer, vibe coder / AI-assisted developer,\n"
        "fullstack engineer with AI features, ML platform / AI infrastructure engineer "
        "(applied, shipping products).\n\n"
        "NOT RELEVANT (label relevant=0):\n"
        '- Pure Data Scientist, ML researcher, NLP/CV research, "train models from scratch"\n'
        "- Generic non-AI roles (backend, frontend, sales, QA, devops, finance)\n"
        '- Not a job vacancy (news, announcements, digests, events, "ищу работу")\n\n'
        "POSITIVE EXAMPLES (relevant roles):\n"
        f"{positive_block}\n\n"
        "NEGATIVE EXAMPLES (not relevant):\n"
        f"{negative_block}\n\n"
        'Return STRICT JSON: {"is_job": 0|1, "relevant": 0|1, "reason": "<=15 words"}\n'
        "relevant=1 requires is_job=1 AND the role matches the candidate profile above."
    )


def _label_one(client: OpenAI, model: str, system_prompt: str, text: str) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:6000]},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        is_job = int(bool(payload.get("is_job", 0)))
        relevant = int(bool(payload.get("relevant", 0)))
        if is_job == 0:
            relevant = 0
        return {
            "is_job": is_job,
            "relevant": relevant,
            "reason": str(payload.get("reason", ""))[:120],
            "labeler": model,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "is_job": 0,
            "relevant": 0,
            "reason": f"error:{exc}"[:120],
            "labeler": model,
        }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"ERROR: dataset not found: {dataset_path}", file=sys.stderr)
        return 1
    if "JOB_FTCH_OPENAI_API_KEY" not in os.environ:
        print("ERROR: JOB_FTCH_OPENAI_API_KEY is required.", file=sys.stderr)
        return 1

    rows = _read_jsonl(dataset_path)
    positive_shots, negative_shots = _load_shots(args.shot_collection)
    system_prompt = _build_system_prompt(positive_shots, negative_shots)
    client = OpenAI(
        api_key=os.environ["JOB_FTCH_OPENAI_API_KEY"],
        base_url=os.environ.get("JOB_FTCH_OPENAI_BASE_URL"),
    )

    unlabeled_indexes = [
        index for index, row in enumerate(rows) if int(row.get("relevant", -1)) == -1
    ]
    if args.limit > 0:
        unlabeled_indexes = unlabeled_indexes[: args.limit]
    print(f"Loaded {len(rows)} rows; labeling {len(unlabeled_indexes)} unlabeled items.")
    if not unlabeled_indexes:
        print("Nothing to do.")
        return 0

    def work(index: int) -> tuple[int, dict[str, Any]]:
        row = rows[index]
        return index, _label_one(client, args.model, system_prompt, str(row.get("text", "") or ""))

    processed = 0
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        for index, label in executor.map(work, unlabeled_indexes):
            rows[index].update(label)
            processed += 1
            if processed % 100 == 0 or processed == len(unlabeled_indexes):
                print(f"  labeled {processed}/{len(unlabeled_indexes)}")
                _write_jsonl(dataset_path, rows)

    _write_jsonl(dataset_path, rows)

    relevant_counter = Counter(int(row.get("relevant", 0)) for row in rows)
    by_source_kind = Counter(str(row.get("source_kind") or "unknown") for row in rows)
    by_source_name = Counter(str(row.get("source_name") or "unknown") for row in rows)

    print(f"\nWrote labels to {dataset_path}")
    print(f"Total: {len(rows)}")
    print(f"Relevant count: {relevant_counter.get(1, 0)}")
    print(f"By source_kind: {dict(by_source_kind)}")
    print("Top source_name counts:")
    for source_name, count in by_source_name.most_common(12):
        print(f"  {source_name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
