---
title: "079 — MCP 2.0 tenant service and CLIProxyAPI LLM path"
description: "**Status**: PROPOSED (rechecked 2026-08-07 against Claudex / multi-model MCP / z.ai patterns)"
updated: 2026-08-07
---
# 079 — MCP 2.0 tenant service and local LLM gateway path

**Status**: ACCEPTED (local MCP + CLIProxy profile implemented on branch)  
**Date**: 2026-08-07  
**Branch**: `feature/mcp-2-cliproxy-service`

## Context

`job_ftch` already ships a runtime MCP adapter (`job_ftch/adapters/mcp/server.py`)
that wraps `TenantRunner` through FastMCP. It exposes multi-tenant tools
(`run_pipeline`, `search_jobs`, source/profile management, …) and three resources
(`jobs://…`, `config://…`).

The product goal for this ADR is a **local-PC stack** where:

1. **Subscriptions** (Codex / ChatGPT OAuth, optionally Claude, Grok, Kimi) and/or
   **cheap coding APIs** (z.ai GLM Coding Plan) power extraction/classification.
2. **MCP** is the control plane for agents to run and inspect the pipeline.
3. **Parsing** still belongs to `TenantRunner` / nodes — not to a shell proxy and
   not to a “second agent” that re-implements ingest.

### Research recheck (2026-08-07)

Community practice (X/Twitter + blogs + GitHub, Jul–Aug 2026) has **three
distinct patterns**. Confusing them was the main risk in the first draft.

#### Pattern A — “Claudex” model gateway (most common for subscriptions)

Popularized by Theo / Tibo (OpenAI Codex team endorsement of CLIProxyAPI):

```text
Coding harness (Claude Code, Cursor, …)
   │  Anthropic/OpenAI wire format
   ▼
CLIProxyAPI  127.0.0.1:8317   ← OAuth for Codex / Claude / Grok / …
   │
   ▼
Vendor model (e.g. gpt-5.6-sol via Codex subscription)
```

- Harness owns tools, files, shell, **and its own MCP clients**.
- CLIProxyAPI is **only** protocol translation + auth for models.
- MCP servers are **siblings** of the model path, not the model path itself.
- Alias example: `ANTHROPIC_BASE_URL=http://127.0.0.1:8317` + `claudex` env wrapper.

#### Pattern B — MCP wrapper that *delegates* to models via CLIProxy

Fresh local projects (Aug 2026):

| Project | Role |
|---|---|
| [multi-model-companion](https://github.com/Reederey87/multi-model-companion) | stdio MCP → CLIProxyAPI; tools like `delegate_review`, `compare_models`; read-only; Claude stays coordinator |
| [llm-cli-gateway](https://mcpservers.org/servers/verivus-oss/llm-cli-gateway) | MCP control plane over many CLIs (Claude/Codex/Gemini/Grok/…) with async jobs |
| `codex mcp-server` | Official: Codex CLI *as* an MCP server (`claude mcp add … codex mcp-server`) — agent-as-tool, not OpenAI base_url |

These are **agent-to-agent / second-opinion** bridges. They do **not** replace a
product pipeline’s structured extract/classify path.

#### Pattern C — Product MCP (what job_ftch already is)

```text
Agent host
   │  MCP tools/resources
   ▼
job_ftch MCP (TenantMCPServer)
   │  TenantRunner
   ▼
LLMProvider  ──OpenAI-compatible──►  local gateway or z.ai / OpenAI
```

Pipeline LLM traffic uses `JOB_FTCH_OPENAI_BASE_URL` (already implemented).
Agent control traffic uses MCP. Same as Pattern A for models, Pattern C for tools.

#### z.ai (GLM Coding Plan) — two official surfaces

From Z.AI docs (not through CLIProxy unless you choose to):

| Surface | Endpoint | Use |
|---|---|---|
| OpenAI Chat Completions | `https://api.z.ai/api/coding/paas/v4` | **job_ftch LLM path** (drop-in base_url) |
| Anthropic Messages | `https://api.z.ai/api/anthropic` | Claude Code harness only |
| Remote MCP search | `https://api.z.ai/api/mcp/web_search_prime/mcp` + Bearer | optional agent tool, not parse core |

z.ai does **not** require CLIProxyAPI. CLIProxy is for **OAuth CLI subscriptions**
(Codex, Claude Code login, Grok Build, …). z.ai is API-key + coding base URL.

#### FastMCP / MCP SDK (Context7)

- Streamable HTTP + bearer headers are the production client/server default.
- FastMCP `create_proxy` / composite proxy can aggregate MCP servers — useful for
  a **local control plane**, but job_ftch product tools stay first-party, not a
  proxy of Codex.
- Python SDK v2: remote MCP acts as OAuth resource server; optional for LAN-only
  stdio/local HTTP with static bearer.

### Hard separation (unchanged, now evidence-backed)

| Concern | Owner | Protocol | Examples |
|---|---|---|---|
| Agent → job_ftch pipeline control | MCP adapter | MCP stdio / Streamable HTTP | `run_pipeline`, `search_jobs` |
| job_ftch → models for extract/classify | `LLMProvider` | OpenAI-compatible HTTP | CLIProxy `:8317/v1` or z.ai `/coding/paas/v4` |
| Agent → second model opinion | **out of scope** companion MCP | MCP → CLIProxy | multi-model-companion |
| Agent harness model | harness env | Anthropic/OpenAI via CLIProxy or z.ai | Claudex alias |

CLIProxyAPI sits on the **LLM HTTP** path.  
job_ftch MCP sits on the **product tools** path.  
They meet only at the local machine, not by nesting MCP inside LLMProvider.

### Current gaps (as of this ADR)

1. CLI `mcp-server` only offers `stdio|http|sse`; `streamable-http` is typed in
   `TenantMCPServer.run` but not wired in argparse.
2. Lifespan is manual: `asyncio.run(startup())` then blocking `run()`, then
   `asyncio.run(shutdown())`. FastMCP supports a proper `lifespan` CM that runs
   before transport I/O; the adapter does not use it.
3. No tool **annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
4. No **prompts** (agent-facing workflows like “triage tenant”, “explain run”).
5. Tools return plain `dict`/`list` without intentional **output schemas** /
   structured content contracts.
6. No MCP-layer auth for network transports (bearer / OAuth resource metadata).
7. No documented **CLIProxyAPI** profile: how to point
   `JOB_FTCH_OPENAI_BASE_URL` + model names at a cliproxy deployment, how to
   health-check it, and how failure modes look for instructor/TOOLS_STRICT.
8. Nested dead package residue under `adapters/mcp/mcp/` (pyc only) is noise.

### Research summary

- **MCP Python SDK v2** renames the high-level class to `MCPServer` and defaults
  remote transport to Streamable HTTP. FastMCP (installed here: 3.4.x) remains a
  production-capable Pythonic server that already implements structured content,
  annotations, lifespan, and HTTP transport on top of the MCP stack.
- Migrating off FastMCP to bare `mcp.server.MCPServer` is **not** required for
  “MCP 2.0 compliance” if FastMCP speaks Streamable HTTP and structured output.
  Dependency churn would be a separate ADR.
- **CLIProxyAPI** exposes Chat Completions / Responses / Claude / Gemini
  dialects. Existing `OpenAIInstructorLLMProvider` already accepts
  `base_url=settings.openai_base_url`. Pointing that URL at cliproxy is the
  first-class integration; a second LLM backend class is only justified if
  cliproxy requires non-OpenAI wire formats for a chosen model family.
- Ecosystem pattern (Grok Search MCP, NanoBanana MCP + CLIProxyAPI): MCP server
  owns product tools; cliproxy owns model credentials/quota. That split matches
  our hexagonal rules.

## Decision

### D1 — Keep MCP as a thin runtime adapter over `TenantRunner`

MCP remains in `job_ftch/adapters/mcp/`. It must not own pipeline policy,
source parsing, store semantics, or LLM selection. All tool bodies call public
application APIs (`TenantRunner` / contracts). Boundary checks continue via
`scripts/check_module_boundaries.py`.

### D2 — Target protocol profile: “MCP 2.0 service mode”

For network deployment the default transport becomes **Streamable HTTP**
(`transport="http"` / FastMCP streamable path, path `/mcp` unless configured).
`stdio` stays the local-dev / desktop-client path. Legacy SSE remains optional
for one release cycle then deprecates.

Service-mode requirements:

1. **Lifespan-owned startup/shutdown** of `TenantRunner` (no double event-loop
   dance outside FastMCP lifespan).
2. **Tool annotations** on every tool:
   - read-only: `get_*`, `list_*`, `search_jobs`, status/lineage
   - non-idempotent write: `run_pipeline`, `run_all_pipelines`, `add_source`,
     `save_profile`, `activate_profile`
   - destructive: `reset_tenant`, `disable_source`
3. **Structured results**: prefer Pydantic models (or typed dicts with stable
   JSON schema) for tool returns so clients receive `structuredContent`.
4. **Prompts** for high-value agent workflows (minimal set first):
   - `triage_tenant` — summarize status + last run + source health
   - `explain_job` — load job + lineage + search context
5. **Progress** for long tools (`run_pipeline`, `run_all_pipelines`) via MCP
   progress notifications when the host supports it; otherwise log + status
   resource.
6. **Auth for non-stdio transports** (phase 2): static bearer for private LAN,
   then OAuth resource-server mode if the product is exposed beyond trusted
   network. No plaintext secrets in MCP client configs (repo-safety rules).

### D3 — Local LLM gateway profiles (CLIProxy and/or z.ai), not a new adapter kind

**Canonical local-PC layout for parsing:**

```text
┌──────────────────────────────── PC (localhost) ────────────────────────────────┐
│                                                                                │
│  Optional agent harness (Claude Code / Codex / Cursor)                         │
│       │ MCP stdio/http                              │ model traffic (optional) │
│       ▼                                             ▼                          │
│  job_ftch MCP  ──tools──► TenantRunner ──LLM──► OpenAIInstructorLLMProvider    │
│  (product)                    │                         │                      │
│                               │                         ├─► CLIProxyAPI :8317  │
│                               │                         │    (Codex OAuth, …)  │
│                               │                         └─► z.ai coding API    │
│                               ▼                              (API key)         │
│                         sources / stores / sinks                               │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Profile A — Codex (or multi-sub) via CLIProxyAPI**

| Setting | Value |
|---|---|
| `JOB_FTCH_LLM_BACKEND` | `openai` |
| `JOB_FTCH_OPENAI_BASE_URL` | `http://127.0.0.1:8317/v1` |
| `JOB_FTCH_OPENAI_API_KEY` | CLIProxy client key |
| `JOB_FTCH_OPENAI_MODEL` | id from `GET /v1/models` (extract/present) |
| `JOB_FTCH_RELEVANCE_LLM_MODEL` | **same gateway id** (relevance judge; must not stay on cloud-only defaults like `gpt-4.1-mini` when using CLIProxy) |
| Instructor mode | `TOOLS` (not `TOOLS_STRICT`) for OpenAI-compatible gateways that reject strict tool schemas with optional fields |

`TenantRunner` builds a **second** `OpenAIInstructorLLMProvider` for the
relevance judge by copying settings and setting `openai_model` to
`relevance_llm_model`. Misalignment with CLIProxy catalog → HTTP 400 on
judge calls → `llm_relevance_unavailable` → all candidates DEFERRED.

**Profile B — z.ai GLM Coding Plan (no CLIProxy required)**

| Setting | Value |
|---|---|
| `JOB_FTCH_LLM_BACKEND` | `openai` |
| `JOB_FTCH_OPENAI_BASE_URL` | `https://api.z.ai/api/coding/paas/v4` |
| `JOB_FTCH_OPENAI_API_KEY` | z.ai API key |
| `JOB_FTCH_OPENAI_MODEL` | GLM coding model id from z.ai console |

**Profile C — hybrid**: CLIProxy as single front door; register z.ai as an
OpenAI-compatible upstream *inside* CLIProxy config if you want one local port
for both OAuth subs and z.ai. job_ftch still only sees `base_url`.

Optional diagnostics (phase 2):

- `JOB_FTCH_LLM_HEALTH_URL` — probe (`/v1/models` or custom)
- `JOB_FTCH_OPENAI_COMPAT_MODE` — `tools_strict|json_schema|text` if instructor
  TOOLS_STRICT fails on a given gateway model

Do **not** add `@register_llm("cliproxy")` or `@register_llm("zai")` until a
wire-format incompatibility is proven. Reuse `openai` + base URL.

Do **not** implement multi-model-companion / codex-as-MCP-tool inside job_ftch.
That is a separate optional agent MCP the operator can install next to us.

### D4 — CLI surface

Extend `job_ftch mcp-server`:

- `--transport {stdio,http,sse,streamable-http}` with `streamable-http` as the
  documented production alias (map to FastMCP’s current HTTP implementation).
- Keep `--host` / `--port`.
- Optional `--auth-bearer-env JOB_FTCH_MCP_BEARER_TOKEN` (phase 2).
- Env: `JOB_FTCH_CONFIGS_DIR` remains required for multi-tenant boot.

### D5 — Packaging and ops

- Extra stays `[mcp]` → `fastmcp` (current constraint `fastmcp>=2.4`; pin lower
  bound to the version that reliably supports lifespan + HTTP after verification).
- Dockerfile continues to run HTTP/service mode.
- Compose profile example: `job_ftch-mcp` + optional external `cliproxy` service
  on a shared network; job_ftch never vendors cliproxy binaries.

### D6 — Explicit non-goals

- Embedding CLIProxyAPI Go SDK inside this Python repo.
- Building multi-model-companion / llm-cli-gateway / `codex mcp-server` clone
  inside job_ftch (agent-to-agent delegation is a separate install).
- Turning product MCP into a shell/command proxy.
- Using z.ai remote search MCP as a substitute for career-site ingest.
- Replacing Telegram bot as the primary end-user product surface.
- Residential “cliproxy.com” IP proxies (unrelated product).
- Forcing Anthropic Messages wire format on `LLMProvider` (harness-only).

## Implementation plan (revised priority)

### Phase 0 — Design lock (this ADR + local recipes)

- Lock Pattern C (product MCP) + gateway LLM profiles A/B/C.
- Document local-PC compose: CLIProxy (optional) + job_ftch mcp-server + env.
- No behavior change required to *use* Profile A/B today if HTTP path already works.

### Phase 1 — Local LLM path proof (highest product value for “parsing via sub”)

1. Docs + `.env.*.example` snippets: CLIProxy Codex profile and z.ai coding profile.
2. Read-only `llm_backend_health` tool (or CLI subcommand) → `GET {base}/models`
   with redacted errors.
3. Smoke script: fixture tenant + one extract/classify call via configured base_url
   (manual; not CI-gated on live keys).
4. Instructor compatibility notes for cliproxy models (TOOLS_STRICT fallback design).

### Phase 2 — MCP service correctness (MCP 2.0 surface)

1. FastMCP **lifespan** for `TenantRunner` startup/shutdown.
2. CLI: `streamable-http` (+ keep stdio for Claude/Cursor desktop).
3. Tool annotations (readOnly / destructive / idempotent).
4. Structured response models for hot tools.
5. Minimal prompts: `triage_tenant`, `explain_job`.
6. Tests + adapter docs.

### Phase 3 — Hardened local/network service

1. Bearer auth for non-stdio transports (mirror z.ai remote MCP header style).
2. Concurrency caps for `run_pipeline`.
3. Progress notifications for long runs.
4. `ai-repo-safety` MCP config scan.

### Operator optional (not shipped in-repo)

- Install CLIProxyAPI + OAuth Codex (and friends) on the same PC.
- Optionally install multi-model-companion *alongside* job_ftch MCP if the agent
  needs second-opinion model tools — never merge codebases.

## Consequences

### Positive

- Agents get a first-class job_ftch service without inventing a parallel REST
  surface for the same `TenantRunner` operations.
- LLM cost/auth can ride CLI OAuth pools via CLIProxyAPI with zero new LLM
  architecture.
- Aligns with hexagonal rules: adapter thin, application owns orchestration.

### Trade-offs / risks

- Streamable HTTP clients differ in session/resumability support; keep `stdio`
  for desktop lockstep.
- Some cliproxy model routes may break instructor `TOOLS_STRICT`; need a
  measured fallback, not silent quality loss.
- Structured output schemas become part of the public MCP contract — version
  them carefully (additive fields only).

### Follow-ups

- If official `mcp` SDK v2 `MCPServer` becomes the project standard, open a
  migration ADR; do not dual-stack.
- If product needs OpenAPI for non-agent HTTP clients, that remains the FastAPI
  adapter, not a fork of MCP tools.

## Success criteria

1. **LLM path**: with only env changes, fixture `run_pipeline` can complete
   extract/classify via (a) CLIProxyAPI+Codex OAuth or (b) z.ai coding base URL.
2. **MCP path**: `mcp-server` (stdio and streamable-http) exposes annotated tools;
   agent can `list_tenants` / `run_pipeline` / `search_jobs` without shell hacks.
3. Lifespan boots tenants before tool I/O; shutdown closes runner cleanly.
4. Architecture verify + MCP tests pass; FastMCP only under `adapters/mcp`.
5. Docs distinguish Claudex (harness models), product MCP (job_ftch), and optional
   multi-model companion MCP — and never recommend nesting them incorrectly.
