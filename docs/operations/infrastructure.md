---
title: "Infrastructure"
description: "Production-shape инфраструктура job_ftch: Docker runtime, Telegram bot compose, Postgres and Qdrant."
updated: 2026-08-02
---
# Infrastructure

Единственный production-shape stack в репозитории сейчас — Telegram bot deploy.
MCP, FastAPI, FastStream и Dagster adapters документированы как adapter
surfaces, но не входят в текущий production compose.

## Stack

| Компонент | Где описан |
| --------- | ---------- |
| Shared runtime image | `docker/runtime/Dockerfile.prod` |
| Bot image | `job_ftch/adapters/telegram_bot/Dockerfile.prod` |
| Compose | `job_ftch/adapters/telegram_bot/docker-compose.prod.yml` |
| Tenant configs | `job_ftch/adapters/telegram_bot/config/tenants/` |
| Runtime overlay | `job_ftch/adapters/telegram_bot/runtime.prod.yaml` |

## Services

- `bot` — Telegram bot and tenant scheduler.
- `postgres` — durable store, outbox and runtime state.
- `qdrant` — vector backend when runtime enables vector/hybrid search.
- volumes — Postgres, Qdrant, bot runtime and model/cache data.

Operational commands and backup/update examples live in [deploy](../deploy.md).
