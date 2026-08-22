---
title: "MCP client setup"
description: "How to point local MCP clients at the job_ftch tenant server."
updated: 2026-08-21
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

`JOB_FTCH_MCP_SURFACE=all|mass|personal` (default `all`) selects 18 / 14 / 12
tools. There are no second names for the same behavior.

Shared: `list_tenants`, `get_status`, `get_runtime`, `doctor`, `get_sources`,
`update_source`, `get_jobs`, `update_shot`.

Mass: `run_pipeline` (`clear_first` replaces standalone `clear_run_data`),
`get_prefilter_status`, `prepare_prefilter_dataset`, `train_prefilter`,
`evaluate_prefilter`, `promote_prefilter` (`rollback=true` rolls back).

Personal: `set_resume`, `probe_page`, `browser_session`, `run_source`.

Removed names include the old 68-tool catalog (`get_tenant_status`,
`add_source`, `add_example`, `run_browser_probe`, search-session tools,
feedback tools, `jobs://` resources, and the rest of the forbidden list in
tests). Use the names above.

## Resources

- `config://{tenant_id}`

## Config directory

The CLI requires `--configs-dir` or `JOB_FTCH_CONFIGS_DIR`. The production bot
tenant directory is `job_ftch/adapters/telegram_bot/config/tenants`.
