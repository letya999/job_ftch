---
title: "MCP deployment"
description: "Three profiles: local process (Win/macOS/Linux/WSL), VPS systemd, Docker."
updated: 2026-08-07
---
# MCP deployment

Three supported ways to run the job_ftch MCP adapter. LLM steps always use
`JOB_FTCH_OPENAI_*` (point at host/local CLIProxyAPI for a Codex subscription).
Observability stays off in these profiles.

| Profile | When | Process model |
|---|---|---|
| **A. Local process** | Dev laptop Win / macOS / Ubuntu / WSL | Foreground CLI or OS service / Task Scheduler |
| **B. VPS** | Always-on remote box | `systemd` unit + streamable-http |
| **C. Docker** | Isolated single container | `docker compose` in `docker/local-mcp/` |

Boundary (all profiles): MCP is a **runtime adapter** over `TenantRunner` only.
It must not own pipeline policy, parsers, or store semantics. See ADR-079.

Default tenant configs for local MCP: `docker/local-mcp/config/tenants`.

---

## Shared env (all profiles)

```bash
# LLM gateway = CLIProxyAPI (Codex OAuth) or any OpenAI-compatible endpoint
export JOB_FTCH_LLM_BACKEND=openai
export JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1   # VPS: use LAN URL of CLIProxy
export JOB_FTCH_OPENAI_API_KEY=cliproxy-local-key
export JOB_FTCH_OPENAI_MODEL=gpt-5.6-codex   # must appear in GET /v1/models

export JOB_FTCH_TRACING_ENABLED=false
export JOB_FTCH_OPENOBSERVE_ENABLED=false
export JOB_FTCH_STORE_BACKEND=sqlite
export JOB_FTCH_JOB_BACKEND=sqlite
export JOB_FTCH_SEARCH_BACKEND=sqlite
export JOB_FTCH_JOB_GROUP_STORE_BACKEND=sqlite
export JOB_FTCH_CONFIGS_DIR=docker/local-mcp/config/tenants
```

Codex CLI client (`~/.codex/config.toml`):

```toml
[mcp_servers.job_ftch]
url = "http://127.0.0.1:8000/mcp"
```

On a remote VPS replace host with the VPS IP/DNS (and TLS reverse proxy if public).

---

## A. Local process (no Docker)

Works on **Windows**, **Ubuntu**, **macOS**, **WSL2**. No container daemon required
for job_ftch itself. CLIProxyAPI usually runs as a separate local process on
`:8317` (OAuth stays on the machine where you logged in).

### Install

```bash
# repo root
uv sync --extra mcp --extra sqlite --extra openai --extra feeds --extra site_scrapers
# optional browsers for career sites:
uv sync --extra browser --extra stealth
uv run patchright install chromium   # or: python -m patchright install chromium
```

Windows PowerShell:

```powershell
uv sync --extra mcp --extra sqlite --extra openai --extra feeds --extra site_scrapers
# optional: uv sync --extra browser --extra stealth; uv run patchright install chromium
```

### Foreground (simplest)

```bash
# Unix / WSL / macOS
./scripts/mcp/run_local.sh

# Windows
.\scripts\mcp\run_local.ps1
```

Equivalent manual command:

```bash
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

stdio for agents that spawn the server themselves:

```bash
uv run job_ftch mcp-server \
  --configs-dir docker/local-mcp/config/tenants \
  --transport stdio
```

### Keep alive without Docker

| OS | Option |
|---|---|
| **Ubuntu / Debian / WSL (systemd)** | Copy `deploy/mcp/job-ftch-mcp.service`, edit paths, `systemctl --user enable --now job-ftch-mcp` |
| **macOS** | `launchd` plist or run in `tmux`/`screen`; see notes in service file header |
| **Windows** | Task Scheduler "At log on" → `run_local.ps1`, or NSSM/WinSW wrapping the same command |
| **Any** | `tmux new -s job_ftch 'uv run job_ftch mcp-server …'` |

There is **no** built-in multi-process supervisor inside job_ftch. One MCP
process loads tenants at startup (FastMCP lifespan → `TenantRunner`) and serves
tools until stopped.

Smoke:

```bash
# after server is up and CLIProxy is up
# from any MCP client, or use Codex tools:
#   llm_backend_health → list_tenants → run_pipeline(tenant_id=local_mcp)
```

---

## B. VPS (systemd)

Target: always-on Linux VPS, MCP on streamable-http, SQLite under `/var/lib/job_ftch`.

1. Clone repo, install `uv`, run `uv sync` with the same extras as local.
2. Place tenant YAML under `/etc/job_ftch/tenants` (or keep repo path).
3. Install unit:

```bash
sudo mkdir -p /var/lib/job_ftch /etc/job_ftch
sudo cp deploy/mcp/job-ftch-mcp.service /etc/systemd/system/
# edit User=, WorkingDirectory=, EnvironmentFile=, paths
sudo systemctl daemon-reload
sudo systemctl enable --now job-ftch-mcp
sudo systemctl status job-ftch-mcp
```

4. Open firewall only as needed (`8000/tcp` to your laptop/VPN, not the open internet
   without auth/TLS).
5. CLIProxyAPI: either on the same VPS (`127.0.0.1:8317`) or on your PC with a
   tunnel. Set `JOB_FTCH_OPENAI_BASE_URL` accordingly in `/etc/job_ftch/mcp.env`.

Example env file `/etc/job_ftch/mcp.env`:

```bash
JOB_FTCH_LLM_BACKEND=openai
JOB_FTCH_OPENAI_BASE_URL=http://127.0.0.1:8317/v1
JOB_FTCH_OPENAI_API_KEY=cliproxy-local-key
JOB_FTCH_OPENAI_MODEL=gpt-5.6-codex
JOB_FTCH_TRACING_ENABLED=false
JOB_FTCH_OPENOBSERVE_ENABLED=false
JOB_FTCH_STORE_BACKEND=sqlite
JOB_FTCH_STORE_PATH=/var/lib/job_ftch/{tenant_id}/store.db
JOB_FTCH_CONFIGS_DIR=/etc/job_ftch/tenants
```

Codex on your laptop:

```toml
[mcp_servers.job_ftch]
url = "http://VPS_IP:8000/mcp"
```

Prefer SSH tunnel for non-public use:

```bash
ssh -L 8000:127.0.0.1:8000 user@vps
# then url = http://127.0.0.1:8000/mcp
```

---

## C. Docker (one container)

No OpenObserve/Langfuse. SQLite volume + Chromium in image. CLIProxy stays on
the **host** (or another host) via `host.docker.internal`.

```bash
cp docker/local-mcp/env.example docker/local-mcp/.env
# edit key + model
docker compose -f docker/local-mcp/docker-compose.yml up --build
```

Details: `docker/local-mcp/README.md`.

Minimal image (no browser install): `job_ftch/adapters/mcp/Dockerfile`.

---

## Transports

| Transport | Use |
|---|---|
| `stdio` | Agent spawns process (Claude Desktop style) |
| `streamable-http` | Codex HTTP MCP, VPS, Docker (default production) |
| `http` / `sse` | Legacy clients |

CLI:

```bash
uv run job_ftch mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

---

## Health and operations

| Check | How |
|---|---|
| MCP tools | Codex: `list_tenants`, `llm_backend_health` |
| LLM gateway | `GET $BASE/models` with gateway client auth header |
| Process | `systemctl status job-ftch-mcp` / Task Manager / `docker ps` |
| Data | SQLite under `JOB_FTCH_STORE_PATH` or Docker volume `job_ftch_mcp_data` |

Stop: Ctrl+C (foreground), `systemctl stop`, or `docker compose down`.
