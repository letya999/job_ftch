# MCP server adapter

A FastMCP server that exposes `job_ftch` tools (`search_jobs`, `run_pipeline`, …)
and `job://` resources to MCP clients (Claude Code, Cursor, Claude Desktop).

## Run locally

```bash
uv sync --extra mcp --extra sqlite --extra openai
uv run job_ftch mcp-server --configs-dir config/tenants --transport http --host 0.0.0.0 --port 8000
```

`--transport stdio` is also supported for direct client integration.

## Run with Docker

```bash
docker build -f adapters/mcp/Dockerfile -t job-ftch-mcp .
docker run -p 8000:8000 --env-file .env -v "$PWD/config:/app/config" job-ftch-mcp
```

## Entry point

The server factory is published under the `job_ftch.mcp_servers` entry-point group
(`adapters.mcp.server:create_server`). `adapters/mcp/adapter.py` keeps a thin
deprecated `create_mcp_server()` shim for newer callers.
