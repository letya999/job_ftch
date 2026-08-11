from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text=(
            "Senior machine learning engineer\n"
            "Remote\n"
            "Company: OpenAI\n"
            "Python, llm, pytorch\n"
            "Salary: USD 120000 - 150000"
        ),
        metadata={"company": "OpenAI", "title": "Senior machine learning engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


class _AcceptingHeuristicLLMProvider(HeuristicLLMProvider):
    async def classify(self, _prompt, _schema):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            decision="accept",
            confidence=0.95,
            reasoning="deterministic MCP fixture",
            matched_positive_aspects=(),
            mismatched_aspects=(),
        )


@pytest.mark.asyncio
async def test_mcp_server_registers_surface_and_serves_tenant_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    profile_path = tmp_path / "profiles.yaml"
    profile_path.write_text(
        """catalog_name: mcp_test
profiles:
  - profile_id: ml
    name: ML Engineer
    target_roles: ["ML Engineer"]
    preferred_skills: ["ML"]
""",
        encoding="utf-8",
    )
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "ai_jobs",
                "display_name": "AI Jobs",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "filter_profile_path": str(profile_path),
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )

    class FakeMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: dict[str, object] = {}
            self.resources: dict[str, object] = {}

        def tool(self, func):  # type: ignore[no-untyped-def]
            self.tools[func.__name__] = func
            return func

        def resource(self, uri: str):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                self.resources[uri] = func
                return func

            return decorator

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            self.run_kwargs = kwargs

    monkeypatch.setitem(sys.modules, "fastmcp", type("FastMCPModule", (), {"FastMCP": FakeMCP}))

    from job_ftch.adapters.mcp.server import create_server
    from job_ftch.application import tenant_runner as tenant_runner_module

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    assert server.runner is not None
    server.runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    assert server.app.name == "job_ftch"
    assert set(server.app.tools) == {
        "activate_profile",
        "add_source",
        "approve_search_session",
        "cancel_search_session",
        "create_search_session",
        "disable_source",
        "explain_browser_route",
        "explain_search_session",
        "get_run",
        "get_job",
        "get_job_lineage",
        "get_search_session_status",
        "get_status",
        "ingest_resume",
        "list_browser_capabilities",
        "list_profiles",
        "list_runs",
        "list_search_results",
        "list_source_health",
        "list_sources",
        "list_tenants",
        "plan_source_routes",
        "reset_tenant",
        "run_all_pipelines",
        "run_pipeline",
        "run_search_session",
        "save_profile",
        "search_jobs",
    }
    assert set(server.app.resources) == {
        "config://{tenant_id}",
        "jobs://{tenant_id}/latest",
        "jobs://{tenant_id}/run_summary",
    }

    run_summary = await server.app.tools["run_pipeline"]("ai_jobs")
    tenant_list = await server.app.tools["list_tenants"]()
    latest_jobs = json.loads(await server.app.resources["jobs://{tenant_id}/latest"]("ai_jobs"))
    status_payload = await server.app.tools["get_status"]("ai_jobs")
    source_health = await server.app.tools["list_source_health"]("ai_jobs")
    saved_profile = await server.app.tools["save_profile"](
        "ai_jobs",
        "1",
        "ml",
        "machine learning engineer",
    )
    listed_profiles = await server.app.tools["list_profiles"]("ai_jobs", "1")
    active_profile = await server.app.tools["activate_profile"]("ai_jobs", "1", "ml")
    added_source = await server.app.tools["add_source"](
        "ai_jobs",
        "https://example.com/jobs",
        None,
        100,
    )
    listed_sources = await server.app.tools["list_sources"]("ai_jobs")
    disabled_source = await server.app.tools["disable_source"](
        "ai_jobs",
        added_source["source_id"],
    )
    run_history = await server.app.tools["list_runs"]("ai_jobs", 10)
    search_results = await server.app.tools["search_jobs"]("senior", "ai_jobs", 10)
    lineage_payload = await server.app.tools["get_job_lineage"](latest_jobs[0]["job_id"], "ai_jobs")
    fetched_run = await server.app.tools["get_run"](run_summary["source_run_id"], "ai_jobs")

    assert run_summary["tenant_id"] == "ai_jobs"
    assert tenant_list[0]["tenant_id"] == "ai_jobs"
    assert latest_jobs[0]["source_name"] == "fixture"
    assert status_payload is not None
    assert status_payload["tenant_id"] == "ai_jobs"
    assert source_health[0]["source_id"] == "debug:fixture"
    assert saved_profile["profile_id"] == "ml"
    assert listed_profiles[0]["active"] is True
    assert active_profile["profile_id"] == "ml"
    assert added_source["source_id"] == "career_site:example_com_jobs"
    assert any(item["source_id"] == "career_site:example_com_jobs" for item in listed_sources)
    assert disabled_source["status"] == "disabled"
    assert len(run_history) == 1
    assert run_history[0]["source_run_id"] == run_summary["source_run_id"]
    assert len(search_results) == 1
    assert lineage_payload is not None
    assert lineage_payload["job_id"] == latest_jobs[0]["job_id"]
    assert lineage_payload["source_run_id"] is not None
    assert fetched_run is not None
    assert fetched_run["source_run_id"] == run_summary["source_run_id"]

    await server.shutdown()


@pytest.mark.asyncio
async def test_mcp_server_real_fastmcp_tool_handlers(tmp_path: Path) -> None:
    pytest.importorskip("fastmcp")

    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "tenant.json").write_text(
        json.dumps(
            {
                "tenant_id": "ai_jobs",
                "display_name": "AI Jobs",
                "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                "store_backend": "sqlite",
                "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
                "job_group_store_backend": "sqlite",
                "job_backend": "sqlite",
                "search_backend": "sqlite",
                "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
            }
        ),
        encoding="utf-8",
    )

    from job_ftch.adapters.mcp.server import create_server

    server = create_server(configs_dir=configs_dir)
    await server.startup()
    assert server.runner is not None

    groups = await server.runner.search_jobs("machine learning", tenant_id="ai_jobs", limit=5)
    assert isinstance(groups, list)

    job = await server.runner.get_job("nonexistent-id")
    assert job is None

    tenants = await server.runner.list_tenants()
    assert isinstance(tenants, list)
    assert len(tenants) > 0

    await server.shutdown()
