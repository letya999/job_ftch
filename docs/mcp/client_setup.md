# MCP Client Setup

## stdio

- Claude Desktop / Cursor / Codex CLI / Codex App:
  point the client to `job_ftch mcp-server --configs-dir ./config/tenants`

## SSE / HTTP

- Remote agents and CI:
  run `job_ftch mcp-server --configs-dir ./config/tenants --transport http --host 0.0.0.0 --port 8000`

The server exposes tools for tenant runs, status, search, job lookup, and tenant reset, plus read-only resources:

- `jobs://{tenant_id}/latest`
- `jobs://{tenant_id}/run_summary`
- `config://{tenant_id}`
