"""Promote a controlled eval artifact to config/recipes/champion.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHAMPION_PATH = ROOT / "config" / "recipes" / "champion.yaml"
CHAMPION_ARTIFACT_PATH = ROOT / "config" / "recipes" / "champion_artifact.json"
RECIPE_PATH = ROOT / "config" / "recipes" / "production_pipeline_recipe.yaml"
PROD_CONFIG_PATH = ROOT / "config" / "runtime.prod.yaml"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path, default=CHAMPION_PATH)
    parser.add_argument("--artifact-out", type=Path, default=CHAMPION_ARTIFACT_PATH)
    return parser.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _build_champion_snapshot(
    artifact: dict[str, Any], source_artifact_path: Path
) -> dict[str, Any]:
    recipe = _load_yaml(RECIPE_PATH)
    return {
        "source_artifact_path": _relative(source_artifact_path),
        "run_at": artifact.get("run_at"),
        "dataset": artifact.get("dataset"),
        "dataset_name": artifact.get("dataset_name"),
        "run_name": artifact.get("run_name"),
        "profile_shots": recipe.get("profile_shots"),
        "provenance": artifact.get("provenance"),
        "summary": artifact.get("summary"),
        "llm": artifact.get("llm"),
        "metrics_delivered": artifact.get("metrics_delivered"),
        "metrics_accept": artifact.get("metrics_accept"),
        "by_source_kind": artifact.get("by_source_kind"),
        "slice_gates": artifact.get("slice_gates"),
    }


def _allowlist_champion_hashes(text: str) -> str:
    comments = {
        "graph_hash": "immutable graph content hash",
        "recipe_id": "recipe identity hash",
        "comparison_key": "comparison identity hash",
        "ontology_hash": "ontology content hash",
        "dataset_sha256": "dataset content hash",
    }
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        key = stripped.split(":", 1)[0]
        if key in comments and "pragma: allowlist secret" not in line:
            line = f"{line}  # pragma: allowlist secret -- {comments[key]}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _require_controlled_artifact(
    artifact: dict[str, Any],
    source_artifact_path: Path,
    champion_artifact_path: Path,
) -> dict[str, Any]:
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError("artifact has no top-level provenance block")
    if provenance.get("dirty_state"):
        raise RuntimeError("dirty-state eval artifacts cannot be promoted")
    if provenance.get("incomplete_candidate_set"):
        raise RuntimeError("incomplete candidate sets cannot be promoted")
    reset = provenance.get("reset")
    if not isinstance(reset, dict) or not reset.get("performed"):
        raise RuntimeError("champion promotion requires a reset-before-run artifact")
    if reset.get("source_snapshots_after_reset") not in (0, None):
        raise RuntimeError("source snapshots were not empty after reset")

    prod_config = _load_yaml(PROD_CONFIG_PATH)
    prod_graph_path = prod_config.get("pipeline_graph_path")
    if provenance.get("graph_path") != prod_graph_path:
        raise RuntimeError(
            "artifact graph_path does not match config/runtime.prod.yaml: "
            f"{provenance.get('graph_path')!r} != {prod_graph_path!r}"
        )

    metrics = artifact.get("metrics_delivered")
    if not isinstance(metrics, dict):
        raise RuntimeError("artifact has no metrics_delivered block")

    graph_hash = provenance.get("graph_hash")
    expected_graph_hash = prod_config.get("pipeline_graph_expected_hash")
    if expected_graph_hash and graph_hash != expected_graph_hash:
        raise RuntimeError(
            "artifact graph_hash does not match runtime.prod.yaml expected hash: "
            f"{graph_hash!r} != {expected_graph_hash!r}"
        )

    llm = artifact.get("llm") or {}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_path": _relative(champion_artifact_path),
        "source_artifact_path": _relative(source_artifact_path),
        "graph_path": provenance["graph_path"],
        "graph_hash": graph_hash,
        "recipe_id": provenance.get("recipe_id"),
        "comparison_key": provenance.get("comparison_key"),
        "ontology_hash": provenance.get("ontology_hash"),
        "state_mode": provenance.get("state_mode"),
        "store_backend": provenance.get("store_backend"),
        "dataset_sha256": provenance.get("dataset_sha256"),
        "sample_size": provenance.get("sample_size"),
        "seed": provenance.get("seed"),
        "candidate_counts": provenance.get("candidate_counts"),
        "incomplete_candidate_set": False,
        "metrics": {
            "precision": float(metrics.get("precision", 0.0)),
            "recall": float(metrics.get("recall", 0.0)),
            "f1": float(metrics.get("f1", 0.0)),
        },
        "llm": {
            "calls": int(llm.get("calls", 0)),
            "cost_usd": float(llm.get("cost_usd", 0.0)),
            "cost_is_complete": bool(llm.get("cost_is_complete", False)),
        },
    }


def main() -> int:
    args = _parse_args()
    artifact_path = args.artifact
    if not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path
    if not artifact_path.exists():
        print(f"ERROR: artifact not found: {artifact_path}", file=sys.stderr)
        return 1

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        champion_artifact_path = args.artifact_out
        if not champion_artifact_path.is_absolute():
            champion_artifact_path = ROOT / champion_artifact_path
        champion = _require_controlled_artifact(
            artifact,
            artifact_path,
            champion_artifact_path,
        )
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: cannot promote champion: {exc}", file=sys.stderr)
        return 1

    champion_snapshot = _build_champion_snapshot(artifact, artifact_path)
    champion_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    champion_artifact_path.write_text(
        json.dumps(champion_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out_path = args.out
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    champion_yaml = yaml.safe_dump(champion, sort_keys=False, allow_unicode=True)
    out_path.write_text(_allowlist_champion_hashes(champion_yaml), encoding="utf-8")
    print(f"Champion recipe: {out_path}")
    print(f"Champion artifact snapshot: {champion_artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
