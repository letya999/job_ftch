from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import job_ftch.application.tenant_runner as tenant_runner_module
from job_ftch.application.builder import load_profile_catalog
from job_ftch.application.graph import compile_graph, load_graph
from job_ftch.application.graph.pipeline_stage import GraphPipelineStage
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig
from job_ftch.domain.profile import ProfileCatalog, SearchProfile

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_tenant_runtime_uses_v2_graph_stage_when_configured(tmp_path: Path) -> None:
    fixture = tmp_path / "items.json"
    fixture.write_text("[]", encoding="utf-8")
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "graph-test",
            "display_name": "Graph test",
            "llm_backend": "heuristic",
            "sources": [{"type": "local_fixture", "path": fixture.as_posix()}],
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "out.json")},
        }
    )
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "embedding_enabled": False,
            "bgem3_enabled": False,
            "pipeline_graph_path": "config/pipelines/evidence_v2.yaml",
            "pipeline_graph_expected_hash": None,
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    runtime = runner.get_runtime("graph-test")

    builder, _snapshot = await runner._build_runtime_builder(
        runtime,
        effective_sources=list(runtime.base_sources),
        catalog=load_profile_catalog(runtime.settings),
        run_id="graph-runtime-test",
        relevance_prompts={},
    )
    pipeline = builder.build()

    assert len(pipeline._nodes) == 1
    assert isinstance(pipeline._nodes[0], GraphPipelineStage)
    await runner.close()


@pytest.mark.asyncio
async def test_tenant_runtime_rejects_unexpected_graph_hash(tmp_path: Path) -> None:
    fixture = tmp_path / "items.json"
    fixture.write_text("[]", encoding="utf-8")
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "graph-hash-test",
            "display_name": "Graph hash test",
            "llm_backend": "heuristic",
            "sources": [{"type": "local_fixture", "path": fixture.as_posix()}],
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "out.json")},
        }
    )
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "embedding_enabled": False,
            "bgem3_enabled": False,
            "pipeline_graph_path": "config/pipelines/evidence_v2.yaml",
            "pipeline_graph_expected_hash": "0" * 64,
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    runtime = runner.get_runtime("graph-hash-test")

    with pytest.raises(RuntimeError, match="graph hash mismatch"):
        await runner._build_runtime_builder(
            runtime,
            effective_sources=list(runtime.base_sources),
            catalog=load_profile_catalog(runtime.settings),
            run_id="graph-runtime-hash-test",
            relevance_prompts={},
        )
    await runner.close()


@pytest.mark.asyncio
async def test_tenant_v2_binding_receives_live_ontology_enriched_catalog_without_expanding_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "items.json"
    fixture.write_text("[]", encoding="utf-8")
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "graph-ontology-test",
            "display_name": "Graph ontology test",
            "llm_backend": "heuristic",
            "sources": [{"type": "local_fixture", "path": fixture.as_posix()}],
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "out.json")},
        }
    )
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "embedding_enabled": False,
            "bgem3_enabled": False,
            "pipeline_graph_path": "config/pipelines/evidence_v2.yaml",
            "pipeline_graph_expected_hash": None,
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)
    runtime = runner.get_runtime("graph-ontology-test")
    runtime.ontology_store = object()

    async def _runtime_ontology(_store: object) -> dict[str, object]:
        return {"anti_patterns": ["not sales roles"], "roles": ["Runtime AI Role"]}

    captured: dict[str, object] = {}
    original_bindings = tenant_runner_module.build_v2_typed_bindings

    def _capture_bindings(**kwargs: object) -> dict[str, object]:
        captured["catalog"] = kwargs["catalog"]
        return original_bindings(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "job_ftch.application.builder._build_runtime_ontology_payload", _runtime_ontology
    )
    monkeypatch.setattr(tenant_runner_module, "build_v2_typed_bindings", _capture_bindings)

    await runner._build_runtime_builder(
        runtime,
        effective_sources=list(runtime.base_sources),
        catalog=ProfileCatalog(
            catalog_name="runtime-ontology-test",
            profiles=(SearchProfile(profile_id="p1", target_roles=("Base Role",)),),
        ),
        run_id="graph-runtime-ontology-test",
        relevance_prompts={},
    )

    profile = captured["catalog"].profiles[0]  # type: ignore[union-attr]
    assert profile.target_roles == ("Base Role",)
    assert "not sales roles" in profile.anti_preferences
    await runner.close()


def test_promoted_graph_matches_runtime_hash() -> None:
    graph = compile_graph(load_graph("config/pipelines/evidence_v2_compact_postaccept.yaml"))
    assert graph.spec.metadata["production_default"] is True
    assert all(node.node != "bgem3_embed" for node in graph.spec.nodes)
    settings = Settings()
    expected = settings.model_copy(
        update={
            "pipeline_graph_expected_hash": (
                "b19e5148f3c84f720a3fd5ac84703a7f2b93561fdd74ed9e05a7955a437ceaf7"  # pragma: allowlist secret -- immutable graph content hash
            )
        }
    ).pipeline_graph_expected_hash
    assert graph.graph_hash == expected
