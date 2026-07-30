from __future__ import annotations

import pytest

from job_ftch.application.ontology_snapshot import (
    OntologyItemReference,
    build_affected_item_report,
    diff_ontology_snapshots,
)
from job_ftch.domain import OntologySnapshot


def _snapshot(payload: str, profile_id: str = "backend") -> OntologySnapshot:
    return OntologySnapshot(tenant_id="tenant", profile_id=profile_id, payload_json=payload)


def test_snapshot_diff_is_recursive_and_deterministic() -> None:
    previous = _snapshot('{"skills":{"python":"Python","sql":"SQL"},"roles":["backend"]}')
    current = _snapshot('{"roles":["backend","platform"],"skills":{"go":"Go","python":"Python"}}')

    diff = diff_ontology_snapshots(previous, current)

    assert diff.previous_version == previous.version
    assert diff.current_version == current.version
    assert [(change.path, change.before, change.after) for change in diff.changes] == [
        ("/roles", '["backend"]', '["backend","platform"]'),
        ("/skills/go", None, "Go"),
        ("/skills/sql", "SQL", None),
    ]


def test_snapshot_diff_rejects_cross_profile_comparison() -> None:
    with pytest.raises(ValueError, match="same tenant and profile"):
        diff_ontology_snapshots(_snapshot("{}"), _snapshot("{}", profile_id="frontend"))


def test_affected_report_selects_prior_version_and_preserves_unmatched_records() -> None:
    previous = _snapshot('{"skills":{"sql":"SQL"}}')
    current = _snapshot('{"skills":{}}')
    diff = diff_ontology_snapshots(previous, current)

    report = build_affected_item_report(
        diff,
        [
            OntologyItemReference("b", previous.version, ("SQL", "Python")),
            OntologyItemReference("a", previous.version, ("Python",)),
            OntologyItemReference("new", current.version, ("SQL",)),
        ],
    )

    assert [item.item_id for item in report.items] == ["a", "b"]
    assert report.items[0].matched_terms == ()
    assert report.items[1].matched_terms == ("sql",)
    assert all(item.requires_replay for item in report.items)
