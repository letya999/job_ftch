from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from scripts.evaluate_classification import evaluate

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_evaluate_classification_uses_keyword_rule_lists(tmp_path: Path) -> None:
    fixture = tmp_path / "classification_eval.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stable_id": "job-1",
                        "source_kind": "telegram_channel",
                        "source_name": "fixture",
                        "external_id": "job-1",
                        "url": "https://example.com/jobs/1",
                        "text": "Вакансия: Senior ML Engineer\nТребования: Python, LLM, production.",
                        "is_job": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "stable_id": "misc-1",
                        "source_kind": "telegram_channel",
                        "source_name": "fixture",
                        "external_id": "misc-1",
                        "url": "https://example.com/post/1",
                        "text": "Обсуждаем рынок AI вакансий и карьерные новости.",
                        "is_job": False,
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = await evaluate(fixture, limit=None)

    assert report["metrics_per_class"]["job_posting"]["precision"] > 0.0
    assert report["metrics_per_class"]["job_posting"]["recall"] > 0.0
