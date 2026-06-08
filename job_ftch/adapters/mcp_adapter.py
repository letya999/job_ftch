"""FastMCP adapter exposing pipeline execution and search resources."""
# mypy: disable-error-code="misc,untyped-decorator"

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from job_ftch.application.builder import PipelineBuilder
from job_ftch.application.contracts import SearchBackend
from job_ftch.domain.source_spec import SourceSpec


def create_mcp_server(
    builder: PipelineBuilder,
    *,
    search_backend: SearchBackend | None = None,
    name: str = "job_ftch",
) -> Any:
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        msg = "FastMCP adapter requires the 'mcp' extra: pip install job-ftch[mcp]"
        raise ImportError(msg) from exc

    server = FastMCP(name)
    source_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)

    @server.tool
    async def run_pipeline(source_spec: dict[str, Any]) -> dict[str, Any]:
        """Run one pipeline execution for a single SourceSpec payload."""
        spec = source_adapter.validate_python(source_spec)
        summary = await builder.clone().sources([spec]).run_async()
        return summary.as_dict()

    @server.resource("job-ftch://jobs/search/{query}")
    async def search_jobs_resource(query: str) -> str:
        """Return search results as JSON for MCP resource readers."""
        if search_backend is None:
            return "[]"
        groups = await search_backend.search(query, limit=20)
        return json.dumps([group.model_dump(mode="json") for group in groups], ensure_ascii=False)

    return server
