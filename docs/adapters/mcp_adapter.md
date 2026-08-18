---
title: "MCP adapter"
description: "FastMCP tenant server exposing pipeline tools and job resources."
updated: 2026-08-18
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

For `stdio`:

- Do not pass `--host` / `--port` (HTTP-only). FastMCP rejects those kwargs on stdio.
- Application logs go to stderr so stdout stays reserved for JSON-RPC.
- Prefer a dedicated offline/temp tenant for smoke tests: `local_fixture` + sqlite
  stores + `JOB_FTCH_LLM_BACKEND=heuristic` + embeddings disabled. Production bot
  tenants may hit network sources or OpenAI and hang/timeout.

`run_pipeline` itself is fast once the process is warm; cold MCP process startup
loads tenant runtime/ontology and can take ~20s before the first tool response.

## Operator tools (new surface only)

This branch intentionally exposes only the Telegram-aligned operator surface.
Legacy MCP tool names (`run_all_pipelines`, `get_status`, `list_sources`,
`list_source_health`, `list_runs`, `get_run`, `list_browser_capabilities`,
`explain_browser_route`, `plan_source_routes`, `get_search_session_status`,
`list_search_results`) are not registered.

| Tool | Role |
|---|---|
| `list_tenants()` | tenant catalog |
| `get_tenant_status(tenant_id)` | status + source degradation + latest run |
| `get_sources(tenant_id, include_health=true, include_diagnostics=true)` | sources with health/diagnostics |
| `add_source(tenant_id, link, source_type=null, limit=100)` | add runtime source |
| `disable_source(tenant_id, source_id)` | disable source |
| `run_pipeline(tenant_id=null, user_id=null, scope="tenant\|all", source_ids=null, max_items=null)` | run one or all tenants |
| `clear_run_data(tenant_id, clear_output_artifacts=true)` | `/run`-like clean of run state and output files without deleting profiles |
| `get_pipeline_status(tenant_id)` | latest pipeline status |
| `list_pipeline_runs(tenant_id=null, limit=20)` | run history |
| `get_pipeline_run(run_id, tenant_id=null)` | single run |
| `list_profiles(tenant_id, user_id)` / `save_profile(...)` / `activate_profile(...)` | candidate profiles |
| `ingest_resume(tenant_id, user_id, resume_text, profile_id=null, activate=true)` | resume ingestion |
| `get_examples_summary(tenant_id, user_id, profile_id=null)` | example counts |
| `list_examples(tenant_id, user_id, profile_id=null, kind="all\|resume\|vacancy", label=null)` | list resume/vacancy examples |
| `add_example(tenant_id, user_id, kind, label, text, profile_id=null, refresh_policy="auto")` | add example + learning refresh |
| `remove_example(tenant_id, user_id, kind, label, index, profile_id=null)` | remove one example |
| `clear_examples(tenant_id, user_id, kind="all\|resume\|vacancy", profile_id=null)` | clear examples |
| `create_search_session(...)` / `plan_search_session` / `approve_search_session` / `run_search_session` | search session workflow |
| `get_search_session(session_id)` / `list_search_session_results` / `explain_search_session` / `cancel_search_session` | search session status/results |
| `search_jobs` / `get_job` / `get_job_lineage` | job lookup |
| `get_bypass_capabilities()` / `get_bypass_routes(...)` | browser/bypass inventory |
| `recommend_runtime_setup(...)` / `validate_runtime_setup(...)` | install/config readiness |
| `get_prefilter_requirements(profile_type=null)` | prefilter dataset contract |
| `reset_tenant(tenant_id)` | dangerous admin reset |

Setup/prefilter tools are read-only: they never install packages, never start
live browser sessions, and never return secret values.

## Config directory

`--configs-dir` is accepted on the `mcp-server` subcommand and as a global CLI
flag. Equivalent env: `JOB_FTCH_CONFIGS_DIR`.

## Boundary

MCP is an adapter over `TenantRunner` and public application services. It should
not duplicate pipeline policy, source parsing, store semantics, or import
infrastructure browser clients directly.
