# MCP server adapter

A FastMCP server that exposes operator-facing `job_ftch` tools
(`get_sources`, `run_pipeline`, `get_bypass_capabilities`, …) and `jobs://`
resources to MCP clients (Claude Code, Cursor, Claude Desktop). This branch
uses the new operator surface only; legacy MCP tool aliases are not registered.
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
(`adapters.mcp.server:create_server`). `adapters/mcp/adapter.py` keeps a thin
deprecated `create_mcp_server()` shim for newer callers.
