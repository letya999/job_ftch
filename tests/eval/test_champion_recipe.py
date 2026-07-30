"""Verify the champion recipe file is consistent with the runtime graph."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHAMPION_PATH = ROOT / "config" / "recipes" / "champion.yaml"
PROD_CONFIG_PATH = ROOT / "config" / "runtime.prod.yaml"


@pytest.fixture()
def champion() -> dict:
    if not CHAMPION_PATH.exists():
        pytest.skip("champion.yaml not yet created")
    return yaml.safe_load(CHAMPION_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def prod_config() -> dict:
    return yaml.safe_load(PROD_CONFIG_PATH.read_text(encoding="utf-8"))


def test_champion_file_exists() -> None:
    assert CHAMPION_PATH.exists(), "config/recipes/champion.yaml must exist"


def test_champion_graph_hash_matches_computed(champion: dict) -> None:
    """The graph_hash in champion.yaml must match the actual compiled graph."""
    from job_ftch.application.graph import compile_graph, load_graph

    graph_path = champion.get("graph_path")
    assert graph_path, "champion.yaml must declare graph_path"

    full_path = ROOT / graph_path
    assert full_path.exists(), f"graph_path does not exist: {graph_path}"

    spec = load_graph(full_path)
    compiled = compile_graph(spec)
    assert champion["graph_hash"] == compiled.graph_hash, (
        f"champion graph_hash {champion['graph_hash']!r} does not match "
        f"computed {compiled.graph_hash!r}"
    )


def test_champion_artifact_exists(champion: dict) -> None:
    """The artifact file referenced by champion.yaml must exist."""
    artifact_path = champion.get("artifact_path")
    assert artifact_path, "champion.yaml must declare artifact_path"
    full_path = ROOT / artifact_path
    assert full_path.exists(), f"artifact not found: {artifact_path}"


def test_champion_metrics_match_artifact(champion: dict) -> None:
    """Metrics in champion.yaml must match the artifact file."""
    artifact_path = champion.get("artifact_path")
    if not artifact_path:
        pytest.skip("no artifact_path in champion.yaml")
    full_path = ROOT / artifact_path
    if not full_path.exists():
        pytest.skip(f"artifact not found: {artifact_path}")
    artifact = json.loads(full_path.read_text(encoding="utf-8"))
    metrics = artifact.get("metrics_delivered", {})
    champion_metrics = champion.get("metrics", {})
    for key in ("precision", "recall", "f1"):
        assert abs(float(champion_metrics.get(key, 0)) - float(metrics.get(key, 0))) < 1e-6, (
            f"champion {key} ({champion_metrics.get(key)}) does not match "
            f"artifact ({metrics.get(key)})"
        )


def test_champion_graph_path_matches_prod(champion: dict, prod_config: dict) -> None:
    """The graph_path in champion.yaml must match runtime.prod.yaml."""
    champion_path = champion.get("graph_path")
    prod_path = prod_config.get("pipeline_graph_path")
    assert champion_path == prod_path, (
        f"champion graph_path {champion_path!r} does not match prod config {prod_path!r}"
    )


def test_champion_not_incomplete(champion: dict) -> None:
    """Champion must not have incomplete_candidate_set."""
    assert not champion.get("incomplete_candidate_set", False), (
        "champion.yaml must not have incomplete_candidate_set=true"
    )


def test_champion_candidate_count_is_complete(champion: dict) -> None:
    """The canonical champion is based on the full 486-candidate eval set."""
    counts = champion.get("candidate_counts", {})
    assert counts.get("total_candidates") == 486
    assert counts.get("expected_total_candidates") == 486
