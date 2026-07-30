---
title: "MCP adapter"
description: "FastMCP tenant server exposing pipeline tools and job resources."
updated: 2026-07-28
---
# MCP adapter

`job_ftch.adapters.mcp` exposes tenant operations to MCP clients.

## Source of truth

- Server: `job_ftch/adapters/mcp/server.py`
- Deprecated shim: `job_ftch/adapters/mcp/adapter.py`
- CLI entry point: `uv run job_ftch mcp-server`
- Dockerfile: `job_ftch/adapters/mcp/Dockerfile`

Use `create_server()` from `job_ftch.adapters.mcp.server` for new code.
`create_mcp_server()` remains only as a deprecated compatibility shim.

## Local HTTP

```bash
uv sync --extra mcp --extra sqlite --extra openai
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport http \
  --host 127.0.0.1 \
  --port 8000
```

## Local stdio

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport stdio
```

## Boundary

MCP is an adapter over `TenantRunner`. It should not duplicate pipeline policy,
source parsing or store semantics.
