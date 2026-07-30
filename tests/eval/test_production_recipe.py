"""Regression contract for the pinned production pipeline recipe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from job_ftch.application.dataset_hashing import dataset_hash

ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = ROOT / "config" / "recipes" / "production_pipeline_recipe.yaml"
CHAMPION_PATH = ROOT / "config" / "recipes" / "champion.yaml"
CHAMPION_ARTIFACT_PATH = ROOT / "config" / "recipes" / "champion_artifact.json"
LIVE_ARTIFACT_PATH = ROOT / "config" / "recipes" / "live_run_terminal_artifact.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_recipe_file_exists() -> None:
    assert RECIPE_PATH.exists()


def test_recipe_graph_matches_runtime_and_compiled_hash() -> None:
    from job_ftch.application.graph import compile_graph, load_graph

    recipe = _load_yaml(RECIPE_PATH)
    runtime = recipe["runtime"]
    prod_config = _load_yaml(ROOT / "config" / "runtime.prod.yaml")

    assert prod_config["pipeline_graph_path"] == runtime["graph_path"]
    assert prod_config["pipeline_graph_expected_hash"] == runtime["graph_hash"]

    compiled = compile_graph(load_graph(ROOT / runtime["graph_path"]))
    assert compiled.graph_hash == runtime["graph_hash"]


def test_recipe_models_and_runtime_flags_are_pinned() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    runtime = recipe["runtime"]
    base_runtime = _load_yaml(ROOT / "config" / "runtime.yaml")
    prod_runtime = _load_yaml(ROOT / "config" / "runtime.prod.yaml")
    tenant = _load_yaml(ROOT / runtime["tenant_config"])

    assert base_runtime["openai_model"] == runtime["openai_model"]
    assert base_runtime["relevance_llm_model"] == runtime["relevance_llm_model"]
    assert prod_runtime["openai_model"] == runtime["openai_model"]
    assert prod_runtime["relevance_llm_model"] == runtime["relevance_llm_model"]
    assert tenant["store_backend"] == runtime["store_backend"]
    assert tenant["job_group_store_backend"] == runtime["job_group_store_backend"]
    assert tenant["job_backend"] == runtime["job_backend"]
    assert tenant["search_backend"] == runtime["search_backend"]
    assert "llm_backend" not in tenant

    for key in ("bgem3_enabled", "relevance_backend", "tracing_enabled", "openobserve_enabled"):
        assert prod_runtime[key] == runtime[key]


def test_env_examples_do_not_override_recipe_terminal_runtime_policy() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    runtime = recipe["runtime"]

    for env_name in (".env.dev.example", ".env.prod.example"):
        env_values = _load_env_example(ROOT / env_name)
        assert (
            env_values["JOB_FTCH_EMBEDDING_ENABLED"].lower()
            == str(runtime["embedding_enabled"]).lower()
        )
        assert env_values["JOB_FTCH_BGEM3_ENABLED"].lower() == str(runtime["bgem3_enabled"]).lower()
        assert env_values["JOB_FTCH_RELEVANCE_BACKEND"] == runtime["relevance_backend"]


def test_profile_shot_contract_is_40_examples() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    shots = recipe["profile_shots"]
    assert shots["total"] == 40
    assert (
        shots["positive_resume"]
        + shots["negative_resume"]
        + shots["positive_jobs"]
        + shots["negative_jobs"]
    ) == shots["total"]

    champion = _load_json(CHAMPION_ARTIFACT_PATH)
    profile_counts = champion["profile_shots"]
    assert profile_counts["positive_resume"] == shots["positive_resume"]
    assert profile_counts["negative_resume"] == shots["negative_resume"]
    assert profile_counts["positive_jobs"] == shots["positive_jobs"]
    assert profile_counts["negative_jobs"] == shots["negative_jobs"]
    assert profile_counts["total"] == shots["total"]


def test_oss_reproduction_fixture_contract_matches_recipe() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    oss = recipe["oss_reproduction"]
    runtime = recipe["runtime"]
    controlled = recipe["controlled_eval"]
    prefilter = recipe["prefilter"]
    live = recipe["live_run"]

    test_user = _load_json(ROOT / oss["test_user_fixture"])
    tenant_fixture = _load_yaml(ROOT / oss["tenant_fixture"])
    tenant_sources = tenant_fixture["sources"]

    assert test_user["telegram_id"] == oss["test_user_telegram_id"]
    assert test_user["tenant_id"] == oss["test_user_tenant_id"] == recipe["tenant_id"]
    assert tenant_fixture["tenant_id"] == recipe["tenant_id"]
    assert len(tenant_sources) == oss["tenant_fixture_source_count"] == live["source_count"]

    source_ids = [f"{source['type']}:{source['source_name']}" for source in tenant_sources]
    assert sorted(source_ids) == sorted(live["source_ids"])
    assert oss["sources_fixture"] == live["sources_fixture"]
    assert oss["controlled_dataset"] == controlled["dataset_path"]
    assert oss["prefilter_training_dataset"] == prefilter["training_dataset"]
    assert oss["graph_path"] == runtime["graph_path"]

    base_runtime = _load_yaml(ROOT / oss["runtime_base"])
    prod_runtime = _load_yaml(ROOT / oss["runtime_prod"])
    settings = oss["required_runtime_settings"]

    assert base_runtime["openai_model"] == settings["openai_model"]
    assert base_runtime["relevance_llm_model"] == settings["relevance_llm_model"]
    assert prod_runtime["pipeline_graph_path"] == settings["pipeline_graph_path"]
    assert prod_runtime["pipeline_graph_expected_hash"] == settings["pipeline_graph_expected_hash"]
    assert prod_runtime["embedding_enabled"] == settings["embedding_enabled"]
    assert prod_runtime["bgem3_enabled"] == settings["bgem3_enabled"]
    assert prod_runtime["relevance_backend"] == settings["relevance_backend"]


def test_prefilter_artifact_matches_recipe() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    prefilter = recipe["prefilter"]
    artifact_path = ROOT / prefilter["artifact_path"]
    artifact = _load_json(artifact_path)

    assert _sha256(artifact_path) == prefilter["artifact_sha256"]
    assert artifact["schema_version"] == prefilter["schema_version"]
    assert artifact["model_version"] == prefilter["model_version"]
    assert artifact["training"]["dataset_sha256"] == prefilter["training_dataset_sha256"]
    assert artifact["training"]["n_rows"] == prefilter["training_rows"]
    assert artifact["training"]["n_positive"] == prefilter["training_positive"]
    assert artifact["training"]["excluded_ids"] == prefilter["excluded_ids"]


def test_controlled_eval_dataset_and_metrics_match_recipe() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    controlled = recipe["controlled_eval"]
    champion = _load_yaml(CHAMPION_PATH)
    champion_artifact = _load_json(CHAMPION_ARTIFACT_PATH)
    provenance = champion_artifact["provenance"]

    assert dataset_hash(ROOT / controlled["dataset_path"]) == controlled["dataset_sha256"]
    assert champion["dataset_sha256"] == controlled["dataset_sha256"]
    assert champion["comparison_key"] == controlled["comparison_key"]
    assert champion["recipe_id"] == controlled["recipe_id"]
    assert champion["ontology_hash"] == controlled["ontology_hash"]
    assert champion["candidate_counts"]["total_candidates"] == controlled["expected_candidates"]
    assert provenance["reset"]["performed"] is True
    assert provenance["reset"]["source_snapshots_after_reset"] == 0
    assert provenance["dirty_state"] is False
    assert provenance["incomplete_candidate_set"] is False

    for key, expected in controlled["metrics"].items():
        assert champion["metrics"][key] == expected
        assert champion["metrics"][key] >= controlled["metric_floors"][key]


def test_controlled_eval_metric_floors_do_not_drop_below_release_policy() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    floors = recipe["controlled_eval"]["metric_floors"]

    assert floors["precision"] >= 0.8
    assert floors["recall"] >= 0.7
    assert floors["f1"] >= 0.75


def test_live_sources_fixture_matches_recipe() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    live = recipe["live_run"]
    source_path = ROOT / live["sources_fixture"]
    payload = _load_json(source_path)
    source_ids = [f"{source['type']}:{source['source_name']}" for source in payload["sources"]]

    assert _sha256(source_path) == live["sources_fixture_sha256"]
    assert len(source_ids) == live["source_count"]
    assert sorted(source_ids) == sorted(live["source_ids"])


def test_live_tenant_sources_match_recipe_fixture() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    live = recipe["live_run"]
    runtime = recipe["runtime"]
    tenant = _load_yaml(ROOT / runtime["tenant_config"])
    source_fixture = _load_json(ROOT / live["sources_fixture"])

    tenant_source_ids = [
        f"{source['type']}:{source['source_name']}" for source in tenant["sources"]
    ]
    fixture_source_ids = [
        f"{source['type']}:{source['source_name']}" for source in source_fixture["sources"]
    ]

    assert len(tenant_source_ids) == live["source_count"]
    assert sorted(tenant_source_ids) == sorted(fixture_source_ids) == sorted(live["source_ids"])
    assert sorted(tenant["sources"], key=lambda source: source["source_name"]) == sorted(
        source_fixture["sources"], key=lambda source: source["source_name"]
    )


def test_live_terminal_artifact_metrics_match_recipe() -> None:
    recipe = _load_yaml(RECIPE_PATH)
    live = recipe["live_run"]
    artifact = _load_json(ROOT / live["tracked_artifact"])

    assert artifact["summary_counts"] == live["summary_counts"]
    assert sorted(artifact["source_failures"]) == sorted(live["source_failures"])
    assert artifact["manual_counts"] == live["manual_counts"]

    labels = artifact["labels"]
    assert len(labels) == sum(live["manual_counts"].values())
    assert (
        sum(1 for label in labels if label["pipeline_bucket"] == "ACCEPT")
        == live["summary_counts"]["emitted"]
    )
    assert (
        sum(1 for label in labels if label["manual_outcome"] == "TP") == live["manual_counts"]["TP"]
    )
    assert (
        sum(1 for label in labels if label["manual_outcome"] == "FP") == live["manual_counts"]["FP"]
    )

    for key, expected in live["metrics"].items():
        assert artifact["metrics"][key] == expected
        assert artifact["metrics"][key] >= live["metric_floors"][key]

    assert artifact["wall_seconds"] == live["latency"]["wall_seconds"]
    assert artifact["llm"]["llm_latency_ms"] == live["latency"]["llm_latency_ms"]
    assert artifact["llm"]["cost_usd"] == live["llm"]["cost_usd"]
