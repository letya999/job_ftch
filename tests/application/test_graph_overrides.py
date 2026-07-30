from __future__ import annotations

import pytest

from job_ftch.application.graph import apply_overrides, load_graph


def test_overrides_are_typed_and_do_not_mutate_loaded_preset() -> None:
    spec = load_graph("config/pipelines/historical_best.yaml")
    updated = apply_overrides(
        spec, params=["semantic_prefilter.dense_margin_threshold=0.07"], enable=["sanitize"]
    )
    original = next(node for node in spec.nodes if node.id == "semantic_prefilter")
    changed = next(node for node in updated.nodes if node.id == "semantic_prefilter")
    assert original.params["dense_margin_threshold"] == 0.05
    assert changed.params["dense_margin_threshold"] == 0.07


def test_unknown_override_node_is_an_error() -> None:
    spec = load_graph("config/pipelines/current_compat.yaml")
    with pytest.raises(ValueError, match="unknown graph node"):
        apply_overrides(spec, disable=["does_not_exist"])
