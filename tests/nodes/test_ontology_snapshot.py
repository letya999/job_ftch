from __future__ import annotations

import json

import pytest

from job_ftch.domain import OntologySnapshot, RawItem, SourceKind
from job_ftch.nodes.ontology_snapshot import OntologySnapshotNode


@pytest.mark.asyncio
async def test_snapshot_node_attaches_canonical_immutable_provenance() -> None:
    snapshot = OntologySnapshot(
        tenant_id="tenant-a",
        profile_id="backend",
        payload_json=' { "skills": ["python"] } ',
    )
    item = RawItem(
        stable_id="raw-1",
        external_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        text="Backend engineer",
    )

    result = await OntologySnapshotNode({"backend": snapshot}).process(item)

    attached = result.metadata["ontology_snapshots"]["backend"]
    assert attached["version"] == snapshot.version
    assert json.loads(attached["payload_json"]) == {"skills": ["python"]}
    assert "ontology_snapshots" not in item.metadata
