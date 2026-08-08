---
title: "MCP client setup"
description: "How to point local MCP clients at the job_ftch tenant server."
updated: 2026-08-07
---
# MCP client setup

## Codex CLI (HTTP)

Preferred local product path: one container from `docker/local-mcp/`, MCP URL:

```toml
# ~/.codex/config.toml
[mcp_servers.job_ftch]
url = "http://127.0.0.1:8000/mcp"
```

See `docker/local-mcp/README.md` and `docker/local-mcp/codex.mcp.example.toml`.

LLM for pipeline steps (not the Codex harness itself): set container env to
CLIProxyAPI on the host (`JOB_FTCH_OPENAI_BASE_URL=http://host.docker.internal:8317/v1`).

## stdio clients

```bash
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport stdio
```

## Streamable HTTP

```bash
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

## Tools (product core, default)

`JOB_FTCH_MCP_SURFACE=core` (default) mirrors the Telegram bot loop:

| Tool | Bot analog |
|------|------------|
| `list_tenants` | `/tenant` |
| `get_status` | `/status` |
| `list_sources` | `/sources` (health embedded) |
| `upsert_source` | add URL; set `replace_source_id` to change |
| `set_source_enabled` | source toggle |
| `add_shot` | `/positive`, `/negative`, `/positive_job`, `/negative_job` |
| `list_shots` / `remove_shot` | `/examples` delete |
| `run_pipeline` | `/run` |
| `search_jobs` | catalog search + filters (`company`, `location`, `work_mode`, `language`, `source_name`, `min_score`, `routing_decision`) |
| `llm_backend_health` | gateway probe |
| `clear_history` | `/clear` (destructive) |

Aliases: `add_source`, `disable_source`.

### Surfaces

| `JOB_FTCH_MCP_SURFACE` | Extra tools |
|------------------------|-------------|
| `core` (default) | product loop only |
| `ops` | + `list_runs`, `get_run`, `get_job`, `get_job_lineage`, `run_all_pipelines`, `list_source_health` |
| `admin` | ops + `list/save/activate_profile`, `reset_tenant` |

## Resources

- `jobs://{tenant_id}/latest`
- `jobs://{tenant_id}/run_summary`
- `config://{tenant_id}`

## Config directory

The CLI requires `--configs-dir` or `JOB_FTCH_CONFIGS_DIR`. Local MCP defaults
to `docker/local-mcp/config/tenants`. Production bot tenants live under
`job_ftch/adapters/telegram_bot/config/tenants`.
