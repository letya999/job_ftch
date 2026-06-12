from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import RawItem, SourceKind

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text="Machine Learning Engineer\nRemote\nCompany: OpenAI\nSalary: USD 120000",
        metadata={"company": "OpenAI", "title": "Machine Learning Engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


@pytest.mark.asyncio
async def test_mcp_server_registers_surface_and_serves_tenant_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    server = create_server(configs_dir=configs_dir)
    await server.startup()

    assert server.app.name == "job_ftch"
    assert set(server.app.tools) == {
        "get_job",
        "get_status",
        "list_tenants",
        "reset_tenant",
        "run_all_pipelines",
        "run_pipeline",
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
    search_results = await server.app.tools["search_jobs"]("machine learning", "ai_jobs", 10)

    assert run_summary["tenant_id"] == "ai_jobs"
    assert tenant_list[0]["tenant_id"] == "ai_jobs"
    assert latest_jobs[0]["source_name"] == "fixture"
    assert status_payload is not None
    assert status_payload["tenant_id"] == "ai_jobs"
    assert len(search_results) == 1

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
