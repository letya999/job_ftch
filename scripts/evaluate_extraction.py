"""Offline extraction evaluation harness for the gold sample fixture.

Per ADR-032: also reports per-field match rate and an LLM-call counter so
regressions in extraction quality or cost can be caught by the eval gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from job_ftch.application.registry import create_llm
from job_ftch.config import Settings
from job_ftch.domain import RawItem
from job_ftch.nodes.extraction import ExtractionNode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate extraction quality against gold samples.",
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
    parser.add_argument(
        "--output",
        default="artifacts/eval/extraction.json",
        help="Where to write the JSON report.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero when field_match_rate < 0.75.",
    )
    return parser.parse_args()


def _iter_fixture_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def evaluate_fixture(settings: Settings, fixture_path: Path) -> dict[str, Any]:
    llm = create_llm(settings)
    extractor = ExtractionNode(llm)  # type: ignore
    records = _iter_fixture_records(fixture_path)
    sample_results: list[dict[str, Any]] = []
    matched_fields = 0
    expected_fields = 0
    per_field_match: Counter[str] = Counter()
    per_field_total: Counter[str] = Counter()
    llm_calls = 0

    for record in records:
        raw_item = RawItem.model_validate(record["raw_item"])
        expected = dict(record["expected"])
        job = await extractor.process(raw_item)
        actual = job.model_dump(mode="json") if job is not None else {}
        if actual.get("title") is None and actual.get("title_raw") is not None:
            actual["title"] = actual["title_raw"]
        if actual.get("company") is None and actual.get("company_name_raw") is not None:
            actual["company"] = actual["company_name_raw"]
        field_matches: dict[str, bool] = {}
        for field, value in expected.items():
            actual_value = actual.get(field)
            is_match = actual_value == value
            field_matches[field] = is_match
            per_field_total[field] += 1
            if is_match:
                per_field_match[field] += 1
            matched_fields += 1 if is_match else 0
            expected_fields += 1
        # Best-effort LLM call counter: the heuristic backend is local + free;
        # any real LLM backend charges one call per item. Counter is conservative.
        if settings.llm_backend != "heuristic":
            llm_calls += 1
        sample_results.append(
            {
                "raw_item_id": raw_item.stable_id,
                "expected": expected,
                "actual": {field: actual.get(field) for field in expected},
                "field_matches": field_matches,
            }
        )

    per_field_rate = {
        field: round(per_field_match[field] / per_field_total[field], 4)
        for field in sorted(per_field_total)
        if per_field_total[field] > 0
    }
    overall = round(matched_fields / expected_fields, 4) if expected_fields else 1.0
    return {
        "fixture": str(fixture_path),
        "llm_backend": settings.llm_backend,
        "samples": len(records),
        "matched_fields": matched_fields,
        "expected_fields": expected_fields,
        "field_match_rate": overall,
        "per_field_match_rate": per_field_rate,
        "llm_calls": llm_calls,
        "llm_calls_per_100_items": round(llm_calls / len(records) * 100, 2) if records else 0.0,
        "gate_passed": overall >= 0.75,
        "results": sample_results,
    }


def _write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


async def _main() -> int:
    args = parse_args()
    base_settings = Settings()
    payload = base_settings.model_dump(mode="python")
    payload["llm_backend"] = args.llm_backend
    settings = Settings.model_validate(payload)
    report = await evaluate_fixture(settings, Path(args.fixture))
    _write_report(report, Path(args.output))

    print("=" * 60)
    print(f"Extraction eval - {report['samples']} samples, llm={report['llm_backend']}")
    print(f"  field_match_rate   : {report['field_match_rate']:.4f} (target >= 0.75)")
    print(f"  llm_calls/100      : {report['llm_calls_per_100_items']}")
    print("  per-field:")
    for field, rate in report["per_field_match_rate"].items():
        print(f"    {field:20s} {rate:.4f}")
    print(f"  report             : {args.output}")
    print("=" * 60)

    if args.gate and not report["gate_passed"]:
        print("GATE FAILED: field_match_rate < 0.75", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
