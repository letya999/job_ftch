from __future__ import annotations

from paritylab.config import LabConfig
from paritylab.routes.fixtures import fixture_routes
from paritylab.routes.probes import probe_routes
from paritylab.store import ArtifactStore


def test_route_builders_have_unique_expected_paths(tmp_path) -> None:
    config = LabConfig(artifacts_dir=tmp_path)
    common = {"x-test": "1"}
    routes = [
        *probe_routes(config, ArtifactStore(tmp_path), common),
        *fixture_routes(config, common),
    ]
    paths = [route.path for route in routes]
    assert len(paths) == len(set(paths))
    assert {
        "/api/probe",
        "/api/events",
        "/api/beacon",
            "/api/opaque",
            "/fixtures/storage-frame",
            "/fixtures/protection/{fixture_id:str}",
        "/api/protection/{fixture_id:str}/contract",
    } == set(paths)
