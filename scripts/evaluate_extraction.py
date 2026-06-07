"""Offline extraction evaluation harness for the gold sample fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from application.registry import create_llm
from config import Settings
from domain import RawItem
from nodes.extraction import ExtractionNode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate extraction quality against gold samples."
    )
    parser.add_argument(
        "--fixture",
        default="fixtures/extraction/gold_samples.jsonl",
        help="Path to the gold sample JSONL fixture.",
    )
    parser.add_argument(
        "--llm-backend",
        default="heuristic",
        help="LLM backend to evaluate, e.g. heuristic or openai.",
    )
    return parser.parse_args()


def _iter_fixture_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def evaluate_fixture(settings: Settings, fixture_path: Path) -> dict[str, Any]:
    llm = create_llm(settings)
    extractor = ExtractionNode(llm)  # type: ignore[arg-type]
    records = _iter_fixture_records(fixture_path)
    sample_results: list[dict[str, Any]] = []
    matched_fields = 0
    expected_fields = 0

    for record in records:
        raw_item = RawItem.model_validate(record["raw_item"])
        expected = dict(record["expected"])
        job = await extractor.process(raw_item)
        actual = job.model_dump(mode="json") if job is not None else {}
        field_matches = {field: actual.get(field) == value for field, value in expected.items()}
        matched_fields += sum(1 for matched in field_matches.values() if matched)
        expected_fields += len(field_matches)
        sample_results.append(
            {
                "raw_item_id": raw_item.stable_id,
                "expected": expected,
                "actual": {field: actual.get(field) for field in expected},
                "field_matches": field_matches,
            }
        )

    return {
        "fixture": str(fixture_path),
        "llm_backend": settings.llm_backend,
        "samples": len(records),
        "matched_fields": matched_fields,
        "expected_fields": expected_fields,
        "field_match_rate": round(matched_fields / expected_fields, 4) if expected_fields else 1.0,
        "results": sample_results,
    }


async def _main() -> int:
    args = parse_args()
    base_settings = Settings()
    payload = base_settings.model_dump(mode="python")
    payload["llm_backend"] = args.llm_backend
    settings = Settings.model_validate(payload)
    report = await evaluate_fixture(settings, Path(args.fixture))
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
