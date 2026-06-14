"""Deprecated shim — use adapters.mcp.server.create_server instead."""

import warnings
from pathlib import Path

from adapters.mcp.server import TenantMCPServer, create_server
from job_ftch.application.builder import PipelineBuilder
from job_ftch.application.contracts import SearchBackend


def create_mcp_server(
    builder: PipelineBuilder,
    *,
    search_backend: SearchBackend | None = None,
    name: str = "job_ftch",
) -> TenantMCPServer:
    warnings.warn(
        "create_mcp_server() is deprecated. Use adapters.mcp.server.create_server() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Fall back to a minimal server that uses a tmp configs dir
    return create_server(configs_dir=Path(".runtime/tenants"), name=name)
