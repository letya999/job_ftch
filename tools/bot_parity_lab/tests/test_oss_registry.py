from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from paritylab.app import create_app
from paritylab.config import LabConfig
from paritylab.oss_registry import OSSRegistryError, load_oss_registry
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_registry_is_reviewed_and_pinned() -> None:
    registry = load_oss_registry(ROOT / "data" / "oss_components.json")
    assert registry.audit() == []
    assert registry.require_evidence_adapter("fingerprintjs").version == "5.2.0"
    with pytest.raises(OSSRegistryError):
        registry.require_evidence_adapter("creep-research")


def test_vendor_evidence_is_versioned_and_namespaced(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        oss_registry_file=ROOT / "data" / "oss_components.json",
        enable_http3=False,
    )
    store = ArtifactStore(config.artifacts_dir)
    app = create_app(config, store=store, registry=TLSConnectionRegistry())
    with TestClient(app) as client:
        response = client.post(
            "/api/vendor/fingerprintjs?sid=vendor-session",
            json={"sequence": 2, "result": {"visitorId": "fixture", "confidence": 0.8}},
        )
        rejected = client.post(
            "/api/vendor/creep-research?sid=vendor-session", json={"result": {}}
        )
    assert response.status_code == 200
    assert response.json()["realm"] == "vendor:fingerprintjs"
    assert rejected.status_code == 400
    state = asyncio.run(store.get("vendor-session"))
    assert state is not None
    record = state.probes[-1]
    assert record.data["version"] == "5.2.0"
    assert record.data["result"] == {"visitorId": "fixture", "confidence": 0.8}
