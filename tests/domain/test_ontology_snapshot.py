from __future__ import annotations

from job_ftch.domain import OntologySnapshot


def test_ontology_snapshot_has_deterministic_immutable_version() -> None:
    left = OntologySnapshot(
        tenant_id="tenant",
        profile_id="profile",
        payload_json='{"roles":["ai engineer"],"skills":["python"]}',
    )
    right = OntologySnapshot(
        tenant_id="tenant",
        profile_id="profile",
        payload_json=' { "skills": ["python"], "roles": ["ai engineer"] } ',
    )

    assert left.version == right.version
    assert left.payload_json == right.payload_json
