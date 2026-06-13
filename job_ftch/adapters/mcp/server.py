"""FastMCP server surface for multi-tenant job_ftch operations."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    if hasattr(value, "model_dump"):
        return cast("Any", value).model_dump(mode="json")
    return str(value)


class TenantMCPServer:
    def __init__(
        self,
        *,
        configs_dir: Path,
        base_settings: Settings | None = None,
        name: str = "job_ftch",
    ) -> None:
        try:
            from fastmcp import FastMCP
        except ImportError as exc:
            msg = "FastMCP server requires the 'mcp' extra: pip install job-ftch[mcp]"
            raise ImportError(msg) from exc

        self.configs_dir = configs_dir
        self.base_settings = base_settings or get_settings()
        self.name = name
        self.app = FastMCP(name)
        self.runner: TenantRunner | None = None
        self._register_surface()

    async def startup(self) -> None:
        if self.runner is not None:
            return
        tenants = load_tenants(self.configs_dir)
        self.runner = TenantRunner.from_tenants(tenants, base_settings=self.base_settings)

    async def shutdown(self) -> None:
        if self.runner is None:
            return
        await self.runner.close()
        self.runner = None

    def run(self, *, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
        self.app.run(transport=transport, host=host, port=port)

    def _register_surface(self) -> None:
        @self.app.tool
        async def run_pipeline(tenant_id: str) -> dict[str, Any]:
            summary = await self._require_runner().run_tenant(tenant_id)
            return summary.as_dict()

        @self.app.tool
        async def run_all_pipelines() -> list[dict[str, Any]]:
            summaries = await self._require_runner().run_all()
            return [summary.as_dict() for summary in summaries]

        @self.app.tool
        async def get_status(tenant_id: str) -> dict[str, Any] | None:
            summary = await self._require_runner().get_status(tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def list_source_health(tenant_id: str) -> list[dict[str, Any]]:
            return await self._require_runner().list_source_health(tenant_id)

        @self.app.tool
        async def list_runs(tenant_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
            summaries = await self._require_runner().list_runs(tenant_id=tenant_id, limit=limit)
            return [summary.as_dict() for summary in summaries]

        @self.app.tool
        async def get_run(run_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
            summary = await self._require_runner().get_run(run_id, tenant_id=tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def list_tenants() -> list[dict[str, Any]]:
            tenants = await self._require_runner().list_tenants()
            return [tenant.model_dump(mode="json") for tenant in tenants]

        @self.app.tool
        async def search_jobs(
            query: str,
            tenant_id: str | None = None,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            limit = min(limit, 100)
            groups = await self._require_runner().search_jobs(
                query, tenant_id=tenant_id, limit=limit
            )
            return [group.model_dump(mode="json") for group in groups]

        @self.app.tool
        async def get_job(job_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
            job = await self._require_runner().get_job(job_id, tenant_id=tenant_id)
            return None if job is None else job.model_dump(mode="json")

        @self.app.tool
        async def get_job_lineage(
            job_id: str,
            tenant_id: str | None = None,
        ) -> dict[str, Any] | None:
            lineage = await self._require_runner().get_job_lineage(job_id, tenant_id=tenant_id)
            return None if lineage is None else lineage.model_dump(mode="json")

        @self.app.tool
        async def reset_tenant(tenant_id: str) -> None:
            await self._require_runner().reset_tenant(tenant_id)

        @self.app.resource("jobs://{tenant_id}/latest")
        async def latest_jobs_resource(tenant_id: str) -> str:
            jobs = await self._require_runner().latest_jobs(tenant_id, limit=10)
            return json.dumps(
                [job.model_dump(mode="json") for job in jobs],
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("jobs://{tenant_id}/run_summary")
        async def run_summary_resource(tenant_id: str) -> str:
            summary = await self._require_runner().get_status(tenant_id)
            return json.dumps(
                None if summary is None else summary.as_dict(),
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("config://{tenant_id}")
        async def config_resource(tenant_id: str) -> str:
            config = await self._require_runner().get_config(tenant_id)
            return json.dumps(config, ensure_ascii=False, default=_json_default)

    def _require_runner(self) -> TenantRunner:
        if self.runner is None:
            msg = "TenantMCPServer.startup() must run before using MCP tools."
            raise RuntimeError(msg)
        return self.runner


def create_server(
    *,
    configs_dir: str | Path,
    base_settings: Settings | None = None,
    name: str = "job_ftch",
) -> TenantMCPServer:
    return TenantMCPServer(
        configs_dir=Path(configs_dir),
        base_settings=base_settings,
        name=name,
    )
