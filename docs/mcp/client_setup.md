# MCP Client Setup

## stdio

- Claude Desktop / Cursor / Codex CLI / Codex App:
  point the client to `job_ftch mcp-server --configs-dir ./config/tenants`

## SSE / HTTP

- Remote agents and CI:
  run `job_ftch mcp-server --configs-dir ./config/tenants --transport http --host 0.0.0.0 --port 8000`

The server exposes tools for tenant runs, run history, status, search, job lookup, lineage, and tenant reset, plus read-only resources:

- `run_pipeline(tenant_id)`
- `run_all_pipelines()`
- `get_status(tenant_id)`
- `list_runs(tenant_id, limit)`
- `get_run(run_id, tenant_id)`
- `search_jobs(query, tenant_id, limit)`
- `get_job(job_id, tenant_id)`
- `get_job_lineage(job_id, tenant_id)`
- `list_tenants()`
- `reset_tenant(tenant_id)`

- `jobs://{tenant_id}/latest`
- `jobs://{tenant_id}/run_summary`
- `config://{tenant_id}`
