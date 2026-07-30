from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.compact_review_artifact import compact_file

if TYPE_CHECKING:
    from pathlib import Path


def test_compact_file_replaces_full_review_rows_atomically(tmp_path: Path) -> None:
    path = tmp_path / "review.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "job_ftch.job.v1",
                "payload": {
                    "stable_id": "job-1",
                    "title": "AI Engineer",
                    "description": "text",
                    "metadata": {
                        "source_run_id": "run-1",
                        "ontology_snapshots": {"default": {"large": "state"}},
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert compact_file(path) == 1

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema_version"] == "job_ftch.review.v1"
    assert record["payload"]["stable_id"] == "job-1"
    assert record["payload"]["source_run_id"] == "run-1"
    assert "metadata" not in record["payload"]
    assert not path.with_suffix(".jsonl.tmp").exists()
