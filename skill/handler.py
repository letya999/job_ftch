"""Local skill helper for routing commands to the job_ftch MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from job_ftch.adapters.mcp.server import create_server

if TYPE_CHECKING:
    from pathlib import Path


async def handle(
    *,
    action: str,
    configs_dir: str | Path,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    server = create_server(configs_dir=configs_dir)
    await server.startup()
    try:
        runner = server._require_runner()
        if action == "list_tenants":
            return {
                "tenants": [tenant.model_dump(mode="json") for tenant in await runner.list_tenants()]
            }
        if action == "run_pipeline" and tenant_id is not None:
            return cast("dict[str, Any]", await server.app.tools["run_pipeline"](tenant_id))
        if action == "get_status" and tenant_id is not None:
            return {
                "status": cast("dict[str, Any] | None", await server.app.tools["get_status"](tenant_id))
            }
        msg = f"Unsupported skill action: {action}"
        raise ValueError(msg)
    finally:
        await server.shutdown()
