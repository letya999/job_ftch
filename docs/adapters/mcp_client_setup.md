---
title: "MCP client setup"
description: "How to point local MCP clients at the job_ftch tenant server."
updated: 2026-07-28
---
# MCP client setup

For local MCP clients, point the client command to the repository CLI:

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport stdio
```

For remote/HTTP clients, run the server separately:

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport http \
  --host 0.0.0.0 \
  --port 8000
```

## Tools

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

## Resources

- `jobs://{tenant_id}/latest`
- `jobs://{tenant_id}/run_summary`
- `config://{tenant_id}`

## Config directory

The CLI requires `--configs-dir` or `JOB_FTCH_CONFIGS_DIR`. The production bot
tenant directory is `job_ftch/adapters/telegram_bot/config/tenants`.
