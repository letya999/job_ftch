---
title: "MCP client setup"
description: "How to point local MCP clients at the job_ftch tenant server."
updated: 2026-08-18
---
# MCP client setup

For local MCP clients, point the client command to the repository CLI:

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport stdio
```

`stdio` keeps JSON-RPC on stdout; logs are written to stderr. Host/port flags
apply only to HTTP/SSE transports.

For offline smoke or CI-style clients, use a temp tenant config with
`local_fixture` sources, sqlite stores, and:

```bash
JOB_FTCH_LLM_BACKEND=heuristic
JOB_FTCH_EMBEDDING_ENABLED=false
JOB_FTCH_EMBEDDING_PREFILTER_ENABLED=false
JOB_FTCH_RELEVANCE_BACKEND=keywords
```

Without those overrides, default OpenAI/embedding settings (or live tenant
sources) can make `run_pipeline` hang waiting on the network.

For remote/HTTP clients, run the server separately:

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport http \
  --host 0.0.0.0 \
  --port 8000
```

## Operator tools

This branch exposes only the new operator surface (no legacy MCP tool aliases).

- `list_tenants()`
- `get_tenant_status(tenant_id)`
- `get_sources(tenant_id, include_health=true, include_diagnostics=true)`
- `add_source(tenant_id, link, source_type=null, limit=100)`
- `disable_source(tenant_id, source_id)`
- `run_pipeline(tenant_id=null, user_id=null, scope="tenant|all", source_ids=null, max_items=null)`
- `clear_run_data(tenant_id, clear_output_artifacts=true)`
- `get_pipeline_status(tenant_id)`
- `list_pipeline_runs(tenant_id, limit)`
- `get_pipeline_run(run_id, tenant_id)`
- `list_profiles` / `save_profile` / `activate_profile` / `ingest_resume`
- `get_examples_summary` / `list_examples` / `add_example` / `remove_example` / `clear_examples`
- `create_search_session` / `plan_search_session` / `approve_search_session` / `run_search_session`
- `get_search_session` / `list_search_session_results` / `explain_search_session` / `cancel_search_session`
- `get_bypass_capabilities()` / `get_bypass_routes(tenant_id, source_id, bypass)`
- `recommend_runtime_setup(tenant_id, source_id, goal, platform)`
- `validate_runtime_setup(goal, tenant_id, source_id)`
- `get_prefilter_requirements(profile_type)`
- `search_jobs(query, tenant_id, limit)`
- `get_job(job_id, tenant_id)`
- `get_job_lineage(job_id, tenant_id)`
- `reset_tenant(tenant_id)`

Removed legacy names include `run_all_pipelines`, `get_status`, `list_sources`,
`list_source_health`, `list_runs`, `get_run`, `list_browser_capabilities`,
`explain_browser_route`, `plan_source_routes`, `get_search_session_status`, and
`list_search_results`. Use the operator names above instead.

## Resources

- `jobs://{tenant_id}/latest`
- `jobs://{tenant_id}/run_summary`
- `config://{tenant_id}`

## Config directory

The CLI requires `--configs-dir` or `JOB_FTCH_CONFIGS_DIR`. The production bot
tenant directory is `job_ftch/adapters/telegram_bot/config/tenants`.
