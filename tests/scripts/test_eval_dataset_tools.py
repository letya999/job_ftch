from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from job_ftch.application.dataset_hashing import dataset_hash

ROOT = Path(__file__).resolve().parents[2]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, f"scripts/eval/{script}", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_dataset_hash_is_independent_of_platform_newlines(tmp_path: Path) -> None:
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    lf.write_bytes(b'{"stable_id":"a"}\n{"stable_id":"b"}\n')
    crlf.write_bytes(b'{"stable_id":"a"}\r\n{"stable_id":"b"}\r\n')

    assert dataset_hash(lf) == dataset_hash(crlf)


def test_validator_reports_historical_label_defects_without_rewriting(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "stable_id": "bad",
                "text": "vacancy",
                "is_job": 0,
                "relevant": 1,
                "reason": "error:429",
            }
        ],
    )
    original = dataset.read_bytes()

    result = _run("validate_eval_dataset.py", str(dataset), "--strict")

    assert result.returncode == 1
    assert "relevant=1 requires is_job=1" in result.stdout
    assert "provider failure must be unknown" in result.stdout
    assert dataset.read_bytes() == original


def test_queue_is_deterministic_and_collects_positive_and_error_rows(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    output = tmp_path / "queue.json"
    _write_jsonl(
        dataset,
        [
            {"stable_id": "a", "text": "same", "is_job": 1, "relevant": 1, "reason": "ok"},
            {"stable_id": "b", "text": "same", "is_job": 0, "relevant": 0, "reason": "timeout"},
        ],
    )

    result = _run("build_adjudication_queue.py", str(dataset), "--output", str(output))

    assert result.returncode == 0, result.stderr
    queue = json.loads(output.read_text(encoding="utf-8"))
    assert len(queue["items"]) == 1
    assert queue["items"][0]["stable_ids"] == ["a", "b"]
    assert queue["items"][0]["reasons"] == [
        "duplicate_content",
        "positive_requires_human_provenance",
        "provider_error",
    ]


def test_queue_prioritizes_pipeline_fp_and_fn_from_replay(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    output = tmp_path / "queue.json"
    replay = tmp_path / "replay.json"
    _write_jsonl(
        dataset,
        [
            {"stable_id": "fp", "text": "first", "is_job": 1, "relevant": 0},
            {"stable_id": "fn", "text": "second", "is_job": 1, "relevant": 1},
        ],
    )
    replay.write_text(
        json.dumps(
            {
                "results": [
                    {"parent_stable_id": "fp", "gold_relevant": 0, "pipeline_accepted": True},
                    {"parent_stable_id": "fn", "gold_relevant": 1, "pipeline_accepted": False},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "build_adjudication_queue.py",
        str(dataset),
        "--output",
        str(output),
        "--replay",
        str(replay),
    )

    assert result.returncode == 0, result.stderr
    items = {item["stable_ids"][0]: item for item in json.loads(output.read_text())["items"]}
    assert items["fp"]["replay_disagreements"] == ["model_false_positive"]
    assert items["fn"]["replay_disagreements"] == ["model_false_negative"]


def test_patch_requires_adjudication_and_writes_a_new_projection(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    output = tmp_path / "v2.jsonl"
    _write_jsonl(
        dataset,
        [{"stable_id": "a", "text": "vacancy", "is_job": 0, "relevant": 0, "reason": "error:429"}],
    )
    validator = _run("validate_eval_dataset.py", str(dataset))
    assert validator.returncode == 0
    report = json.loads(validator.stdout)
    content_hash = __import__("hashlib").sha256(b"vacancy").hexdigest()
    patch = tmp_path / "patch.json"
    patch.write_text(
        json.dumps(
            {
                "original_dataset_hash": report["sha256"],
                "entries": [
                    {
                        "stable_id": "a",
                        "content_hash": content_hash,
                        "old": {"is_job": 0, "relevant": 0},
                        "new": {"is_job": "unknown", "relevant": "unknown"},
                        "status": "adjudicated",
                        "adjudicator": "reviewer",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _run("apply_label_patch.py", str(dataset), str(patch), "--output", str(output))

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["is_job"] == "unknown"
    assert not output.samefile(dataset)


def test_patch_rejects_unadjudicated_entries(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_jsonl(dataset, [{"stable_id": "a", "text": "vacancy", "is_job": 0, "relevant": 0}])
    patch = tmp_path / "patch.json"
    patch.write_text(
        json.dumps({"original_dataset_hash": "wrong", "entries": []}), encoding="utf-8"
    )

    result = _run(
        "apply_label_patch.py", str(dataset), str(patch), "--output", str(tmp_path / "out.jsonl")
    )

    assert result.returncode != 0
    assert "hash mismatch" in result.stderr


def test_validator_accepts_adjudicated_override_of_provider_error(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "stable_id": "reviewed",
                "text": "not a vacancy",
                "is_job": 0,
                "relevant": 0,
                "reason": "error: 429 rate limit",
                "label_history": [
                    {
                        "status": "adjudicated",
                        "adjudicator": "user-authorized-codex-review",
                        "new": {"is_job": 0, "relevant": 0},
                    }
                ],
            }
        ],
    )

    result = _run("validate_eval_dataset.py", str(dataset), "--strict")

    assert result.returncode == 0


def test_deterministic_provider_failure_repair_is_narrow_and_append_only(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    patch = tmp_path / "patch.json"
    output = tmp_path / "repaired.jsonl"
    _write_jsonl(
        dataset,
        [
            {"stable_id": "bad", "text": "vacancy", "is_job": 0, "relevant": 0, "reason": "429"},
            {
                "stable_id": "good",
                "text": "vacancy two",
                "is_job": 0,
                "relevant": 0,
                "reason": "manual",
            },
        ],
    )

    generated = _run("build_provider_failure_patch.py", str(dataset), "--output", str(patch))
    assert generated.returncode == 0, generated.stderr
    assert len(json.loads(patch.read_text(encoding="utf-8"))["entries"]) == 1

    applied = _run("apply_label_patch.py", str(dataset), str(patch), "--output", str(output))
    assert applied.returncode == 0, applied.stderr
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["is_job"] == rows[0]["relevant"] == "unknown"
    assert rows[1]["is_job"] == rows[1]["relevant"] == 0
    assert _run("validate_eval_dataset.py", str(output), "--strict").returncode == 0


def test_deterministic_positive_jobness_repair_only_restores_label_invariant(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    patch = tmp_path / "patch.json"
    output = tmp_path / "repaired.jsonl"
    _write_jsonl(
        dataset,
        [
            {"stable_id": "bad", "text": "vacancy", "is_job": 0, "relevant": 1},
            {"stable_id": "good", "text": "other", "is_job": 0, "relevant": 0},
        ],
    )

    generated = _run("build_positive_jobness_patch.py", str(dataset), "--output", str(patch))
    assert generated.returncode == 0, generated.stderr
    assert len(json.loads(patch.read_text(encoding="utf-8"))["entries"]) == 1

    applied = _run("apply_label_patch.py", str(dataset), str(patch), "--output", str(output))
    assert applied.returncode == 0, applied.stderr
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["is_job"] == rows[0]["relevant"] == 1
    assert rows[1]["is_job"] == rows[1]["relevant"] == 0
    assert _run("validate_eval_dataset.py", str(output), "--strict").returncode == 0
