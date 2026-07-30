from __future__ import annotations

import numpy as np
import pytest

from job_ftch.application.graph.preflight import ShotSnapshot, shot_snapshot_from_vectors


def test_shot_preflight_rejects_empty_user_shots() -> None:
    snapshot = ShotSnapshot("tenant", "user", "user_db_only", "shots", 0, 2, 1024, "model", "hash")
    with pytest.raises(ValueError, match="at least one"):
        snapshot.validate()


def test_shot_preflight_rejects_fixture_source() -> None:
    snapshot = ShotSnapshot("tenant", "user", "fixture", "shots", 1, 1, 1024, "model", "hash")
    with pytest.raises(ValueError, match="user_db_only"):
        snapshot.validate()


def test_vector_snapshot_is_stable_and_excludes_shot_text() -> None:
    snapshot = shot_snapshot_from_vectors(
        tenant_id="tenant",
        user_id="user",
        source_mode="user_db_only",
        collection="shots",
        positive=np.array([[1.0, 2.0]], dtype=np.float32),
        negative=np.array([[3.0, 4.0]], dtype=np.float32),
        model_id="bge-m3",
    )
    assert snapshot.dimensions == 2
    assert len(snapshot.snapshot_hash) == 64
