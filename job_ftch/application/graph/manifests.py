"""Reproducibility manifest helpers; they never contain shot text or secrets."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .contracts import CompiledGraph


def build_run_manifest(
    *,
    graph: CompiledGraph,
    dataset: str | Path,
    seed: int,
    selected_item_ids: list[str],
    runtime: dict[str, Any],
    shot_snapshot: dict[str, Any],
    immutable_resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(dataset)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": {"path": str(path), "sha256": _sha256(path)},
        "seed": seed,
        "selected_item_ids_hash": _hash_texts(selected_item_ids),
        "selected_item_count": len(selected_item_ids),
        "graph": graph.as_dict(),
        "git": _git_state(),
        "runtime": runtime,
        "shots": shot_snapshot,
        "immutable_resources": _resource_hashes(immutable_resources or {}),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_texts(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _resource_hashes(resources: dict[str, Any]) -> dict[str, str]:
    """Hash manifest inputs without serialising profile text or secrets into output."""
    import json

    return {
        name: hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        for name, value in sorted(resources.items())
    }


def _git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()

    try:
        diff = subprocess.check_output(
            ["git", "diff", "--binary", "HEAD"], stderr=subprocess.DEVNULL
        )
        return {
            "commit": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain")),
            "dirty_diff_sha256": hashlib.sha256(diff).hexdigest(),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
