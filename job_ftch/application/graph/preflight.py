"""Read-only preflight contracts for reproducible evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShotSnapshot:
    tenant_id: str
    user_id: str
    source_mode: str
    collection: str
    positive_count: int
    negative_count: int
    dimensions: int
    model_id: str
    snapshot_hash: str

    def validate(self) -> None:
        if self.source_mode != "user_db_only":
            raise ValueError("comparative eval requires user_db_only shots")
        if self.positive_count < 1 or self.negative_count < 1:
            raise ValueError("user shots must contain at least one positive and one negative")
        if self.dimensions < 1 or not self.model_id or not self.snapshot_hash:
            raise ValueError("shot preflight is incomplete")


def dataset_sha256(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def assert_dataset_unchanged(path: str | Path, before: str) -> str:
    after = dataset_sha256(path)
    if after != before:
        raise RuntimeError(f"dataset checksum changed during experiment: {before} -> {after}")
    return after


def shot_snapshot_from_vectors(
    *,
    tenant_id: str,
    user_id: str,
    source_mode: str,
    collection: str,
    positive: Any,
    negative: Any,
    model_id: str,
) -> ShotSnapshot:
    """Create a privacy-preserving stable snapshot from already read-only vectors."""
    positive_count = int(getattr(positive, "shape", (len(positive),))[0])
    negative_count = int(getattr(negative, "shape", (len(negative),))[0])
    dimensions = int(
        getattr(positive, "shape", (0, 0))[1] if getattr(positive, "ndim", 0) > 1 else 0
    )
    digest = sha256()
    for matrix in (positive, negative):
        encoder = getattr(matrix, "tobytes", None)
        digest.update(encoder() if encoder is not None else str(matrix).encode())
    snapshot = ShotSnapshot(
        tenant_id,
        user_id,
        source_mode,
        collection,
        positive_count,
        negative_count,
        dimensions,
        model_id,
        digest.hexdigest(),
    )
    snapshot.validate()
    return snapshot
