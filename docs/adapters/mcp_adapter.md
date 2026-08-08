---
title: "MCP adapter"
description: "FastMCP tenant server exposing pipeline tools and job resources."
updated: 2026-08-09
---
# MCP adapter

`job_ftch.adapters.mcp` exposes tenant operations to MCP clients (Codex CLI,
Claude Code, Cursor, …).

## Source of truth

- Server: `job_ftch/adapters/mcp/server.py`
- Deprecated shim: `job_ftch/adapters/mcp/adapter.py`
- CLI entry point: `uv run job_ftch mcp-server`
- Local one-container profile: `docker/local-mcp/` (SQLite + browsers, no O11y)
- Minimal Dockerfile: `job_ftch/adapters/mcp/Dockerfile`
- ADR: `docs/adr/079-mcp-2-cliproxy-service.md`

Use `create_server()` from `job_ftch.adapters.mcp.server` for new code.
`create_mcp_server()` remains only as a deprecated compatibility shim.

## Codex CLI + CLIProxy (recommended local path)

LLM steps only: point OpenAI settings at host CLIProxyAPI (Codex OAuth).

```bash
# Host: CLIProxyAPI on :8317 with Codex login
export JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export JOB_FTCH_OPENAI_API_KEY=cliproxy-local-key
export JOB_FTCH_OPENAI_MODEL=gpt-5.4-mini
# Required: relevance judge uses this model separately (see TenantRunner)
export JOB_FTCH_RELEVANCE_LLM_MODEL=gpt-5.4-mini
export JOB_FTCH_OPENAI_TIMEOUT_SECONDS=120
export JOB_FTCH_TRACING_ENABLED=false
export JOB_FTCH_OPENOBSERVE_ENABLED=false

docker compose -f docker/local-mcp/docker-compose.yml up --build
```

Codex MCP (`~/.codex/config.toml`):

```toml
[mcp_servers.job_ftch]
url = "http://127.0.0.1:8000/mcp"
```

From Codex: `llm_backend_health` → `list_tenants` → `run_pipeline(tenant_id=local_mcp)`.

Full recipe: `docker/local-mcp/README.md`.

## Local HTTP / Streamable HTTP

```bash
uv sync --extra mcp --extra sqlite --extra openai
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

## Local stdio

```bash
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport stdio
```

## Boundary

MCP is an adapter over `TenantRunner`. It should not duplicate pipeline policy,
source parsing or store semantics. CLIProxyAPI is an LLM gateway only, not an
MCP transport.

Allowed dependencies of `job_ftch/adapters/mcp/`:

- `job_ftch.application.*` (TenantRunner, loaders, input builders)
- `job_ftch.domain.*` / `job_ftch.config`
- optional `fastmcp` / `mcp.types` (lazy)
- stdlib + `httpx` for `probe_llm_backend` only

Forbidden: pipeline nodes, infrastructure scrapers/stores, Telegram bot code.

## Deploy profiles

Three profiles are documented in [mcp_deploy.md](mcp_deploy.md):

1. **Local process** (Windows / macOS / Ubuntu / WSL) — `scripts/mcp/run_local.*`
2. **VPS systemd** — `deploy/mcp/job-ftch-mcp.service`
3. **Docker** — `docker/local-mcp/`
