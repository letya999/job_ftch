# MCP server adapter

A FastMCP server that exposes an 18-tool operator catalog
(`list_tenants`, `get_status`, `get_runtime`, `doctor`, `get_sources`, `update_source`,
`get_jobs`, `update_shot`, plus mass/personal extras) and `config://{tenant_id}`
to MCP clients (Claude Code, Cursor, Claude Desktop).
Gate with `JOB_FTCH_MCP_SURFACE=all|mass|personal` (default `all`).
See `docs/adapters/mcp_adapter.md`.

## Run locally

```bash
uv sync --extra mcp --extra sqlite --extra openai
uv run job_ftch mcp-server --configs-dir config/tenants --transport http --host 0.0.0.0 --port 8000
```

`--transport stdio` is also supported for direct client integration. Do not pass
`--host` / `--port` with stdio (HTTP-only). Logs go to stderr so JSON-RPC on
stdout stays parseable.

For offline smoke, point `--configs-dir` at a temp tenant with `local_fixture`
sources and set `JOB_FTCH_LLM_BACKEND=heuristic` with embeddings disabled.

## Run with Docker

```bash
docker build -f adapters/mcp/Dockerfile -t job-ftch-mcp .
docker run -p 8000:8000 --env-file .env -v "$PWD/config:/app/config" job-ftch-mcp
```

## Entry point

The server factory is published under the `job_ftch.mcp_servers` entry-point group
(`adapters.mcp.server:create_server`).
