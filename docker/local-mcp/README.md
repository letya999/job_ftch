---
title: "Local MCP container (Codex + CLIProxy + SQLite)"
description: "One container: job_ftch MCP, SQLite, browsers. LLM via host CLIProxyAPI/Codex sub."
updated: 2026-08-09
---
# Local MCP container (Codex + CLIProxy + SQLite)

One Docker service runs the **job_ftch MCP server** with:

- **SQLite** for store / jobs / search (no Postgres, DuckDB, LanceDB)
- **Chromium** (patchright) for career-site paths
- **No OpenObserve / Langfuse / OTEL exporters**
- **LLM only** via OpenAI-compatible HTTP → **CLIProxyAPI on the host** (Codex OAuth subscription)
- **Default tenant sources**: the canonical 17-source set from
  `fixtures/sources/ai_jobs.json` (5 Telegram + 12 career sites) in
  `config/tenants/local_mcp.yaml`

```text
Codex CLI ──MCP HTTP──► job_ftch container :8000/mcp
                              │
                              ├─ sources / sqlite / browser  (in container)
                              └─ LLM steps ──HTTP──► host CLIProxyAPI :8317
                                                         └─ Codex subscription
```

CLIProxy stays on the **host** so OAuth logins and quota stay with your normal
desktop CLIProxy setup. The container does not embed CLIProxyAPI.

The normal tenant directory contains exactly the 17 `ai_jobs` sources. A
separate MCP-only browser probe lives under `config/browser-probe`; start the
server with that directory and call `run_pipeline` for tenant `browser_probe`
to verify a real Patchright navigation without changing production source
policy.

## 1. Host: CLIProxyAPI + Codex login

Install and run [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) on the
PC (port **8317** by default). Complete **Codex / ChatGPT OAuth**. Set a client
API key in CLIProxy config.

Check models:

```bash
# set CLIPROXY_API_KEY to the client key configured in CLIProxyAPI
curl -s -H "Authorization: Bearer ${CLIPROXY_API_KEY}" http://127.0.0.1:8317/v1/models
```

Pick a model id that appears in that list. Set **both**:

| Env | Used by |
|-----|---------|
| `JOB_FTCH_OPENAI_MODEL` | extraction / present / general structured LLM |
| `JOB_FTCH_RELEVANCE_LLM_MODEL` | **relevance judge only** (`TenantRunner` rebuilds a second provider) |

If `RELEVANCE_LLM_MODEL` stays at the default `gpt-4.1-mini` while CLIProxy
only exposes Codex ids, every judge call returns HTTP 400, decisions
**DEFER** with `llm_relevance_unavailable`, and ACCEPT stays at 0. Align both
to the same gateway id (example: `gpt-5.4-mini`).

Telegram sources need Telethon `api_id` / `api_hash` (and session) on the host;
without them, run career sites only via `source_ids` or disable Telegram
entries.

## 2. Container

```bash
cp docker/local-mcp/env.example docker/local-mcp/.env
# edit KEY + MODEL

docker compose -f docker/local-mcp/docker-compose.yml up --build
```

MCP endpoint: `http://127.0.0.1:8000/mcp`

## 3. Codex CLI → MCP

Merge `docker/local-mcp/codex.mcp.example.toml` into `~/.codex/config.toml`:

```toml
[mcp_servers.job_ftch]
url = "http://127.0.0.1:8000/mcp"
```

Restart Codex. From the agent:

1. `llm_backend_health` — gateway reachable, model listed
2. `list_tenants` — should show `local_mcp`
3. `run_pipeline` with `tenant_id=local_mcp`
4. `search_jobs` — ACCEPT catalog only (may be empty if nothing accepted)
5. `list_review_jobs` / `list_rejected` — compact operational outcomes
   (enabled for this tenant via `review_output.backend: store` and
   `rejected_output.backend: store`)

Only extract/classify/present LLM calls use the Codex subscription through
CLIProxy. Fetch/scrape/sqlite stay local.

## 4. Without Docker (stdio)

```bash
uv sync --extra mcp --extra sqlite --extra openai --extra browser --extra feeds --extra site_scrapers
export JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export JOB_FTCH_OPENAI_API_KEY=cliproxy-local-key
export JOB_FTCH_OPENAI_MODEL=gpt-5.4-mini
export JOB_FTCH_RELEVANCE_LLM_MODEL=gpt-5.4-mini
export JOB_FTCH_OPENAI_TIMEOUT_SECONDS=120
export JOB_FTCH_TRACING_ENABLED=false
export JOB_FTCH_OPENOBSERVE_ENABLED=false

uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

## Other profiles (no Docker / VPS)

Full matrix (local process, VPS systemd, Docker): [docs/adapters/mcp_deploy.md](../../docs/adapters/mcp_deploy.md).

| Profile | Entry |
|---|---|
| Local process | `scripts/mcp/run_local.sh` / `run_local.ps1` |
| VPS systemd | `deploy/mcp/job-ftch-mcp.service` |
| Docker | this directory |

## Design choices

| Choice | Why |
|---|---|
| SQLite | Already first-class; zero extra services; fine for single-user local |
| Not DuckDB/Lance | No vector/analytics need for this MCP control plane |
| CLIProxy on host | OAuth/desktop login; container only needs base_url |
| No observability stack | Explicit local profile; tracing flags forced off |
| streamable-http | Current MCP remote default for Codex/HTTP clients |

See also ADR `docs/adr/079-mcp-2-cliproxy-service.md`.
