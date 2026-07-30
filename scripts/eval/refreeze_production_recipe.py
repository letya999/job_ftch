"""Recompute every pinned hash in the production recipe from on-disk artifacts.

Running this after any change to pinned artifacts keeps the recipe in sync
without manual editing of hex strings.  It also updates the graph hash in
config/runtime.prod.yaml so the two sources of truth stay aligned.

Usage:
    uv run python scripts/eval/refreeze_production_recipe.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = ROOT / "config" / "recipes" / "production_pipeline_recipe.yaml"
RUNTIME_PROD_PATH = ROOT / "config" / "runtime.prod.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compile_graph_hash(graph_path: Path) -> str:
    from job_ftch.application.graph import compile_graph, load_graph

    compiled = compile_graph(load_graph(graph_path))
    return compiled.graph_hash


def _dataset_hash(path: Path) -> str:
    from job_ftch.application.dataset_hashing import dataset_hash

    return dataset_hash(path)


def _update_yaml_hash(text: str, key: str, new_hash: str) -> str:
    """Replace a hash value in YAML while preserving inline comments."""
    pattern = re.compile(
        rf"^(\s*{re.escape(key)}:\s*)"
        r"[0-9a-f]{32,128}"
        r"(\s*#.*)?$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"key {key!r} with hex hash not found in YAML")
    return pattern.sub(rf"\g<1>{new_hash}\2", text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Refreeze production recipe pins.")
    ap.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    args = ap.parse_args()

    recipe = yaml.safe_load(RECIPE_PATH.read_text(encoding="utf-8"))

    # 1. Graph hash
    graph_path = ROOT / recipe["runtime"]["graph_path"]
    graph_hash = _compile_graph_hash(graph_path)

    # 2. Prefilter artifact hash
    prefilter_path = ROOT / recipe["prefilter"]["artifact_path"]
    prefilter_sha = _sha256(prefilter_path)

    # 3. Training dataset hash (read from artifact, not raw file sha256)
    import json

    artifact = json.loads(prefilter_path.read_bytes())
    training_ds_sha = artifact["training"]["dataset_sha256"]

    # 4. Controlled eval dataset hash (domain-level hash, not raw sha256)
    controlled_ds_path = ROOT / recipe["controlled_eval"]["dataset_path"]
    controlled_ds_sha = _dataset_hash(controlled_ds_path)

    # 5. Sources fixture hash
    sources_path = ROOT / recipe["live_run"]["sources_fixture"]
    sources_sha = _sha256(sources_path)

    updates = {
        "recipe.runtime.graph_hash": (recipe["runtime"]["graph_hash"], graph_hash),
        "recipe.oss_reproduction.pipeline_graph_expected_hash": (
            recipe["oss_reproduction"]["required_runtime_settings"]["pipeline_graph_expected_hash"],
            graph_hash,
        ),
        "recipe.prefilter.artifact_sha256": (recipe["prefilter"]["artifact_sha256"], prefilter_sha),
        "recipe.prefilter.training_dataset_sha256": (
            recipe["prefilter"]["training_dataset_sha256"],
            training_ds_sha,
        ),
        "recipe.controlled_eval.dataset_sha256": (
            recipe["controlled_eval"]["dataset_sha256"],
            controlled_ds_sha,
        ),
        "recipe.live_run.sources_fixture_sha256": (
            recipe["live_run"]["sources_fixture_sha256"],
            sources_sha,
        ),
    }

    changed = []
    for label, (old_val, new_val) in updates.items():
        if old_val != new_val:
            changed.append(label)
            print(f"  {label}:")
            print(f"    old: {old_val}")
            print(f"    new: {new_val}")
        else:
            print(f"  {label}: unchanged")

    if not changed:
        print("\nAll hashes match. Nothing to update.")
        return 0

    if args.dry_run:
        print(f"\n[dry-run] {len(changed)} hash(es) would change. Pass without --dry-run to apply.")
        return 0

    # Rewrite recipe
    recipe_text = RECIPE_PATH.read_text(encoding="utf-8")
    recipe_text = recipe_text.replace(recipe["runtime"]["graph_hash"], graph_hash)
    recipe_text = recipe_text.replace(recipe["prefilter"]["artifact_sha256"], prefilter_sha)
    recipe_text = recipe_text.replace(
        recipe["prefilter"]["training_dataset_sha256"], training_ds_sha
    )
    recipe_text = recipe_text.replace(
        recipe["controlled_eval"]["dataset_sha256"], controlled_ds_sha
    )
    recipe_text = recipe_text.replace(recipe["live_run"]["sources_fixture_sha256"], sources_sha)
    RECIPE_PATH.write_text(recipe_text, encoding="utf-8")
    print(f"\nUpdated {RECIPE_PATH}")

    # Rewrite runtime.prod.yaml and runtime.dev.yaml
    for runtime_path in (RUNTIME_PROD_PATH, ROOT / "config" / "runtime.dev.yaml"):
        if not runtime_path.exists():
            continue
        rt_text = runtime_path.read_text(encoding="utf-8")
        try:
            rt_text = _update_yaml_hash(rt_text, "pipeline_graph_expected_hash", graph_hash)
        except ValueError:
            continue
        runtime_path.write_text(rt_text, encoding="utf-8")
        print(f"Updated {runtime_path}")

    # Rewrite champion.yaml
    champion_path = ROOT / "config" / "recipes" / "champion.yaml"
    if champion_path.exists():
        champion_text = champion_path.read_text(encoding="utf-8")
        old_graph = recipe["runtime"]["graph_hash"]
        old_ds = recipe["controlled_eval"]["dataset_sha256"]
        if old_graph in champion_text:
            champion_text = champion_text.replace(old_graph, graph_hash)
        if old_ds in champion_text:
            champion_text = champion_text.replace(old_ds, controlled_ds_sha)
        champion_path.write_text(champion_text, encoding="utf-8")
        print(f"Updated {champion_path}")

    print(f"\n{len(changed)} hash(es) updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
