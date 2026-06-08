from __future__ import annotations

from pathlib import Path

import pytest

from job_ftch.config import Settings
from scripts.evaluate_extraction import evaluate_fixture


@pytest.mark.asyncio
async def test_extraction_evaluation_harness_reports_gold_fixture_matches() -> None:
    report = await evaluate_fixture(
        Settings.model_validate({"llm_backend": "heuristic"}),
        Path("fixtures/extraction/gold_samples.jsonl"),
    )

    assert report["samples"] == 2
    assert report["expected_fields"] == 4
    assert report["matched_fields"] >= 3
    assert report["field_match_rate"] >= 0.75
