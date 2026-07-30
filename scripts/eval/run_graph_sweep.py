"""Prepare or execute paired graph experiments with one reproducible sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from job_ftch.application.graph import compile_graph, load_graph


def _graph_diff(baseline: dict[str, object], candidate: dict[str, object]) -> dict[str, list[str]]:
    differences: dict[str, list[str]] = {"nodes": [], "resources": [], "metadata": []}
    base_nodes = {
        str(item["id"]): item for item in baseline.get("nodes", []) if isinstance(item, dict)
    }
    candidate_nodes = {
        str(item["id"]): item for item in candidate.get("nodes", []) if isinstance(item, dict)
    }
    for node_id in sorted(set(base_nodes) | set(candidate_nodes)):
        if base_nodes.get(node_id) != candidate_nodes.get(node_id):
            differences["nodes"].append(node_id)
    if baseline.get("resources") != candidate.get("resources"):
        differences["resources"].append("resources")
    base_metadata = dict(baseline.get("metadata") or {})
    candidate_metadata = dict(candidate.get("metadata") or {})
    for key in sorted(set(base_metadata) | set(candidate_metadata)):
        if key not in {"purpose", "experiment", "decision_policy"} and base_metadata.get(
            key
        ) != candidate_metadata.get(key):
            differences["metadata"].append(key)
    return differences


def _validate_declared_variable(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    experiment = dict(candidate.get("metadata") or {}).get("experiment")
    differences = _graph_diff(baseline, candidate)
    if not isinstance(experiment, dict) or not experiment.get("variable"):
        return {
            "validated": False,
            "reason": "candidate has no metadata.experiment.variable",
            "diff": differences,
        }
    variable = str(experiment["variable"])
    valid = not differences["resources"] and not differences["metadata"]
    if variable == "decision_policy":
        valid = valid and not differences["nodes"]
    elif variable.startswith("node:") and "." in variable[5:]:
        node_id, field = variable[5:].split(".", 1)
        valid = valid and differences["nodes"] == [node_id]
        base_node = next(
            (item for item in baseline.get("nodes", []) if item.get("id") == node_id), {}
        )
        candidate_node = next(
            (item for item in candidate.get("nodes", []) if item.get("id") == node_id), {}
        )
        allowed = {field}
        if field == "authority":
            allowed |= {"effect", "shadow"}
        if field.startswith("params."):
            parameter = field.removeprefix("params.")
            allowed = {"params"}
            base_params = dict(base_node.get("params") or {})
            candidate_params = dict(candidate_node.get("params") or {})
            changed_params = {
                key
                for key in set(base_params) | set(candidate_params)
                if base_params.get(key) != candidate_params.get(key)
            }
            valid = valid and changed_params == {parameter}
        valid = (
            valid
            and {
                key
                for key in set(base_node) | set(candidate_node)
                if base_node.get(key) != candidate_node.get(key)
            }
            <= allowed
        )
    else:
        valid = False
    if not valid:
        raise ValueError(f"candidate violates one-variable contract ({variable}): {differences}")
    return {"validated": True, "variable": variable, "diff": differences}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", action="append", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("fixtures/dataset/eval_dataset.jsonl"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--item-ids-hash", required=False, help="Precomputed immutable selected-item hash."
    )
    parser.add_argument(
        "--shot-snapshot-hash", required=False, help="Preflighted tenant/user shot snapshot hash."
    )
    parser.add_argument("--execute", action="store_true", help="Run the evaluator for every graph.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing JSON outputs after manifest validation.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("results/graph_sweeps"))
    parser.add_argument("--sample", type=int, default=400)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--profile-source", default="tenant")
    parser.add_argument("--state-mode", default="runtime", choices=("memory", "runtime"))
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--user-id", default="480637186")
    parser.add_argument("--no-langfuse", action="store_true")
    args = parser.parse_args()
    dataset_hash = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    graphs = [args.baseline, *args.candidate]
    payload = {
        "mode": "execute" if args.execute else "dry_run_only",
        "dataset": str(args.dataset),
        "dataset_sha256": dataset_hash,
        "seed": args.seed,
        "graphs": [
            {"path": str(path), "graph_hash": compile_graph(load_graph(path)).graph_hash}
            for path in graphs
        ],
        "same_item_ids_and_shot_snapshot": bool(args.item_ids_hash and args.shot_snapshot_hash),
        "selected_item_ids_hash": args.item_ids_hash,
        "shot_snapshot_hash": args.shot_snapshot_hash,
        "evaluator_started": False,
    }
    compiled_graphs = [compile_graph(load_graph(path)) for path in graphs]
    variable_reports = [
        _validate_declared_variable(compiled_graphs[0].as_dict(), candidate.as_dict())
        for candidate in compiled_graphs[1:]
    ]
    payload["one_variable"] = (
        variable_reports[0] if len(variable_reports) == 1 else variable_reports
    )
    payload["one_variable_candidates"] = variable_reports
    if args.execute and any(not report.get("validated") for report in variable_reports):
        raise ValueError("execute requires a candidate metadata.experiment.variable declaration")
    if args.execute:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        common = [
            sys.executable,
            "scripts/eval/run_pipeline_eval.py",
            "--dataset",
            str(args.dataset),
            "--seed",
            str(args.seed),
            "--profile-source",
            args.profile_source,
            "--state-mode",
            args.state_mode,
            "--tenant-id",
            args.tenant_id,
            "--user-id",
            args.user_id,
        ]
        if args.full:
            common.append("--full")
        else:
            common.extend(["--sample", str(args.sample)])
        if args.no_langfuse:
            common.append("--no-langfuse")
        output_files: list[str] = []
        expected_item_hash: str | None = None
        expected_shot_hash: str | None = None
        for index, graph_path in enumerate(graphs):
            output = args.out_dir / f"{index:02d}_{graph_path.stem}_seed{args.seed}.json"
            command = [*common, "--graph", str(graph_path), "--out", str(output)]
            if expected_item_hash:
                command.extend(["--expected-selected-item-ids-hash", expected_item_hash])
            if expected_shot_hash:
                command.extend(["--expected-shot-snapshot-hash", expected_shot_hash])
            if not (args.resume and output.exists()):
                subprocess.run(command, check=True)
            output_files.append(str(output))
            manifest = json.loads(output.read_text(encoding="utf-8")).get("experiment_manifest", {})
            if args.resume and (
                manifest.get("dataset_sha256") != dataset_hash
                or int(manifest.get("seed", args.seed)) != args.seed
            ):
                raise RuntimeError(f"resume manifest mismatch for {output}")
            expected_item_hash = str(manifest.get("selected_item_ids_hash"))
            expected_shot_hash = str((manifest.get("shots") or {}).get("snapshot_hash"))
        manifests = [
            json.loads(Path(path).read_text(encoding="utf-8")).get("experiment_manifest", {})
            for path in output_files
        ]
        item_hashes = {manifest.get("selected_item_ids_hash") for manifest in manifests}
        shot_hashes = {
            ((manifest.get("shots") or {}).get("snapshot_hash")) for manifest in manifests
        }
        if (
            len(item_hashes) != 1
            or None in item_hashes
            or len(shot_hashes) != 1
            or None in shot_hashes
        ):
            raise RuntimeError(
                f"paired run drift detected: item_hashes={item_hashes}, shot_hashes={shot_hashes}"
            )
        comparisons = []
        for candidate_path in output_files[1:]:
            compared = subprocess.check_output(
                [
                    sys.executable,
                    "scripts/eval/compare_policy_runs.py",
                    output_files[0],
                    candidate_path,
                ],
                text=True,
                encoding="utf-8",
            )
            comparisons.append(json.loads(compared))
        payload.update(
            {
                "evaluator_started": True,
                "same_item_ids_and_shot_snapshot": True,
                "outputs": output_files,
                "selected_item_ids_hash": next(iter(item_hashes)),
                "shot_snapshot_hash": next(iter(shot_hashes)),
                "comparisons": comparisons,
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
