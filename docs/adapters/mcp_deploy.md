---
title: "MCP deployment"
description: "Local, HTTP, Docker and systemd notes for the FastMCP tenant server."
updated: 2026-07-28
---
# MCP deployment

## Local stdio

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport stdio
```

## Local HTTP

```bash
uv run job_ftch mcp-server \
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants \
  --transport http \
  --host 127.0.0.1 \
  --port 8000
```

## Docker

Build the MCP image from the adapter Dockerfile:

```bash
docker build -f job_ftch/adapters/mcp/Dockerfile -t job-ftch-mcp .
docker run --rm -p 8000:8000 --env-file .env.dev \
  -v "%cd%/job_ftch/adapters/telegram_bot/config/tenants:/app/config" \
  job-ftch-mcp
```

Inside the image, `/app/config` is the tenant config directory.

## systemd

Run the same CLI command under a dedicated service user. Set
`JOB_FTCH_CONFIGS_DIR` instead of hardcoding `--configs-dir` if the service file
already manages environment variables.
