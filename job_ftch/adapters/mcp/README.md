# MCP server adapter

A FastMCP server that exposes `job_ftch` tools (`search_jobs`, `run_pipeline`, …)
and `jobs://` resources to MCP clients (Codex CLI, Claude Code, Cursor).

## Recommended: one container + CLIProxy (Codex subscription for LLM only)

```bash
# Host: CLIProxyAPI on :8317 with Codex OAuth
cp docker/local-mcp/env.example docker/local-mcp/.env
docker compose -f docker/local-mcp/docker-compose.yml up --build
```

Codex `~/.codex/config.toml`:

```toml
[mcp_servers.job_ftch]
url = "http://127.0.0.1:8000/mcp"
```

Details: `docker/local-mcp/README.md`.

## Run locally (no Docker)

```bash
uv sync --extra mcp --extra sqlite --extra openai
export JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export JOB_FTCH_OPENAI_API_KEY=cliproxy-local-key
export JOB_FTCH_OPENAI_MODEL=gpt-5.6-codex
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

`--transport stdio` is also supported for direct client integration.

## Entry point

The server factory is published under the `job_ftch.mcp_servers` entry-point group
(`adapters.mcp.server:create_server`). `adapters/mcp/adapter.py` keeps a thin
deprecated `create_mcp_server()` shim for newer callers.
