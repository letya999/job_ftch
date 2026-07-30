from __future__ import annotations

from pathlib import Path

import pytest

from job_ftch.config import Settings
from scripts.evaluate_extraction import evaluate_fixture

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_extraction_evaluation_harness_reports_gold_fixture_matches() -> None:
    report = await evaluate_fixture(
        Settings.model_validate({"llm_backend": "heuristic"}),
        _REPO_ROOT / "fixtures" / "extraction" / "gold_samples.jsonl",
    )

    # Per ADR-032: gold_samples.jsonl now has 50+ samples (was 2).
    assert report["samples"] >= 50
    assert report["expected_fields"] >= 100
    assert report["matched_fields"] >= 80
    assert report["field_match_rate"] >= 0.75
    assert "per_field_match_rate" in report
    assert "llm_calls_per_100_items" in report
