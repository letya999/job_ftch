from __future__ import annotations

import json

from scripts.eval.build_split_manifest import build_manifest, regression_ids_from_sample
from scripts.eval.validate_split_manifest import validate


def test_split_manifest_keeps_duplicate_content_in_one_split(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    rows = [
        {"stable_id": "a", "text": "Same vacancy"},
        {"stable_id": "b", "text": " same   vacancy "},
        {"stable_id": "c", "text": "Different vacancy"},
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    manifest = build_manifest(dataset, {"a"})
    output = tmp_path / "splits.json"
    output.write_text(json.dumps(manifest), encoding="utf-8")

    assert manifest["splits"]["regression"] == ["a", "b"]
    assert validate(dataset, output) == []


def test_split_manifest_detects_cross_split_duplicate(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"stable_id":"a","text":"Same"}\n{"stable_id":"b","text":"same"}\n',
        encoding="utf-8",
    )
    manifest = build_manifest(dataset, set())
    source_split = next(split for split, ids in manifest["splits"].items() if "b" in ids)
    target_split = next(split for split in manifest["splits"] if split != source_split)
    manifest["splits"][target_split].append("b")
    output = tmp_path / "splits.json"
    output.write_text(json.dumps(manifest), encoding="utf-8")

    assert any("multiple splits" in error for error in validate(dataset, output))


def test_regression_sample_ids_are_deterministic(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "".join(f'{{"stable_id":"{index}","text":"{index}"}}\n' for index in range(10)),
        encoding="utf-8",
    )

    selected = regression_ids_from_sample(dataset, sample_size=4, seed=42)

    assert selected == regression_ids_from_sample(dataset, sample_size=4, seed=42)
    assert len(selected) == 4


def test_manifest_keeps_a_source_family_in_one_promotion_split(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    rows = [
        {"stable_id": "a", "text": "first", "source_kind": "telegram", "source_name": "one"},
        {"stable_id": "b", "text": "second", "source_kind": "telegram", "source_name": "one"},
        {"stable_id": "c", "text": "third", "source_kind": "career", "source_name": "two"},
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    manifest = build_manifest(dataset, set())
    assigned = {stable_id: split for split, ids in manifest["splits"].items() for stable_id in ids}

    assert assigned["a"] == assigned["b"]
    assert manifest["temporal_partitioning"]["status"] == "unavailable"


def test_manifest_recognizes_capture_timestamp_as_temporal_provenance(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "stable_id": "fresh",
                "text": "fresh vacancy",
                "source_kind": "telegram",
                "source_name": "one",
                "fetched_at": "2026-07-15T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = build_manifest(dataset, set())

    assert manifest["temporal_partitioning"] == {
        "status": "available",
        "fields": ["fetched_at"],
        "missing_row_count": 0,
    }


def test_manifest_membership_is_independent_of_input_row_order(tmp_path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    rows = [
        {"stable_id": "a", "text": "same", "source_kind": "telegram", "source_name": "one"},
        {"stable_id": "b", "text": "same", "source_kind": "career", "source_name": "two"},
        {"stable_id": "c", "text": "other", "source_kind": "telegram", "source_name": "one"},
    ]
    first.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    second.write_text("\n".join(json.dumps(row) for row in reversed(rows)) + "\n", encoding="utf-8")

    def memberships(path) -> dict[str, str]:
        return {
            stable_id: split
            for split, ids in build_manifest(path, set())["splits"].items()
            for stable_id in ids
        }

    assert memberships(first) == memberships(second)


def test_validator_can_require_clean_labels_and_temporal_partition(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "stable_id": "a",
                "text": "vacancy",
                "source_kind": "telegram",
                "source_name": "one",
                "is_job": 0,
                "relevant": 0,
                "reason": "error: 429",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "splits.json"
    manifest_path.write_text(json.dumps(build_manifest(dataset, set())), encoding="utf-8")

    errors = validate(
        dataset,
        manifest_path,
        require_clean_labels=True,
        require_temporal_partition=True,
    )

    assert any("provider failure" in error for error in errors)
    assert "temporal partitioning is unavailable" in errors


def test_validator_can_require_adjudicated_positive_provenance(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "stable_id": "positive",
                "text": "vacancy",
                "source_kind": "telegram",
                "source_name": "one",
                "is_job": 1,
                "relevant": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "splits.json"
    manifest_path.write_text(json.dumps(build_manifest(dataset, set())), encoding="utf-8")

    errors = validate(dataset, manifest_path, require_adjudicated_positive_provenance=True)

    assert errors == ["positive lacks adjudicated provenance: positive"]
