---
title: "MCP adapter"
description: "FastMCP tenant server exposing pipeline tools and job resources."
updated: 2026-09-04
---
# MCP adapter

`job_ftch.adapters.mcp` exposes tenant operations to MCP clients.

## Source of truth

- Server: `job_ftch/adapters/mcp/server.py`
- Factory: `create_server()` / `TenantMCPServer`
- CLI entry point: `uv run job_ftch mcp-server`
- Dockerfile: `job_ftch/adapters/mcp/Dockerfile`

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

## Operator tools

`JOB_FTCH_MCP_SURFACE=all|mass|personal` (default `all`) selects the catalog.
`all` is 20 tools, `mass` is 16, `personal` is 14. Shared tools exist on every
surface. Application services stay; only MCP registration shrinks. Tests keep a
forbidden-name list so extra names are not registered.

### Shared (`all`, `mass`, `personal`)

| Tool | Role |
|---|---|
| `list_tenants()` | tenant catalog |
| `get_status(tenant_id, run_id=null)` | tenant snapshot + latest/recent runs + source degradation; one run when `run_id` is set |
| `get_runtime()` | live engines / LLM / CLIProxy / residential proxy / captcha readiness (no secrets) |
| `doctor()` | written diagnosis of extras, browsers, proxies, captcha, CLIProxy; `get_runtime` is the structured subset |
| `get_sources(tenant_id)` | sources with health, assessment, and recommended_route |
| `update_source(tenant_id, action=add\|update\|remove, ...)` | add / patch enabled+limit / remove (config/base delete → `unsupported`) |
| `set_source_important(tenant_id, source_id, important=true, note=null)` | pin/unpin a source as operator-important |
| `list_source_quality(tenant_id)` | current important / reliable / rich / high_relevance lists |
| `get_jobs(tenant_id, query=null, job_id=null, limit=20, include_lineage=false)` | latest, search, or one job |
| `update_shot(..., action=list\|add\|remove\|clear\|compile)` | shots. `list`/`clear` default to all kinds; `add`/`remove` require `kind`+`label` |

### Mass only

| Tool | Role |
|---|---|
| `run_pipeline(tenant_id=null, source_ids=null, max_items=null, clear_first=false, user_id=null, scope="tenant\|all")` | run one or all tenants; `clear_first` wipes run state and output files |
| `get_prefilter_status(tenant_id, profile_id=null)` | dirty flag, current/previous artifacts, dataset contract |
| `prepare_prefilter_dataset(...)` | build JSONL from examples/feedback/eval/mixed |
| `train_prefilter(..., dry_run=true)` | train artifact; default does not write |
| `evaluate_prefilter(...)` | holdout/dataset gate |
| `promote_prefilter(..., rollback=false)` | gated promotion; `rollback=true` restores previous |

### Personal only

| Tool | Role |
|---|---|
| `set_resume(tenant_id, user_id, resume_text, profile_id=null, activate=true)` | ingest resume + shot sync / prefilter dirty |
| `probe_page(..., what=listing\|detail\|challenge\|fingerprint)` | live page probe; **not** ingest |
| `browser_session(action=open\|status\|wait\|solve\|goto\|capture\|close, ...)` | operator browser session; no cookie values |
| `run_source(..., escalation=adaptive\|all, session_id=null, personal_mode=false)` | adaptive ingest, strict parser pin, or `fallback_order` sweep. Personal mode applies `max_items` after final fan-out/dedup |

Search sessions, vacancy feedback, standalone setup/bypass inventory tools, and
`jobs://` resources are not registered. Use `get_jobs` / `get_status` /
`get_runtime` / `doctor` instead. `config://{tenant_id}` remains.

`get_runtime` probes `/models` on the OpenAI-compatible gateway, a cheap hop
through the first configured residential proxy, and captcha key presence. It
never returns proxy URLs, users, passwords, API keys, or cookie values.
Heuristic LLM is ok without a gateway. `doctor` reuses the same probes and
adds extras, public bypass inventory, proxy flags (http list / gateway /
residential hop), and a multi-line `report`. `ok` is false when the OpenAI
backend fails `/models` or every browser engine is missing.

Source ingest still reuses `TenantRunner.run_tenant(..., source_ids=...)`.
`probe_page` is a bounded live probe (not ingest). Pin one bypass with
`run_source(bypass=...)` or walk `fallback_order` with `escalation=all`.
`run_source(session_id=...)` ingests through the already-open operator
session page (same Python page object, not a second Chromium and not cookie
copy). The session stays open after ingest; close it with
`browser_session(action=close)`. `escalation=all` walks the full registered
ladder (`noop` → `cloak`); the attached session engine reuses that tab, other
tiers do not. `engine=playwright` / `stealth_browser` launches vanilla
Playwright when Patchright is not required. Each ingest attempt returns
`parse` (`stage` + `reason`), the six-state `verdict`, and `parser_provenance`
(requested/actual parser, fallback chain, generic usage, URL/card counts). An
explicit special parser pin cannot continue through generic monitors or
scrapers. Missing extras return `unavailable`. MCP does
not import Playwright/Patchright/nodriver clients.

## Config directory

`--configs-dir` is accepted on the `mcp-server` subcommand and as a global CLI
flag. Equivalent env: `JOB_FTCH_CONFIGS_DIR`.

## Boundary

MCP is an adapter over `TenantRunner` and public application services. It should
not duplicate pipeline policy, source parsing, store semantics, or import
infrastructure browser clients directly.
