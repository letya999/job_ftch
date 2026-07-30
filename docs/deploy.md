---
title: "Деплой Telegram bot production stack"
description: "Production-shape деплой job_ftch: shared runtime image, Telegram bot compose, Postgres и Qdrant."
updated: 2026-07-28
---
# Деплой Telegram bot production stack

Единственный production-shape deploy в репозитории сейчас — Telegram bot stack:

- shared runtime image: `docker/runtime/Dockerfile.prod`;
- bot image: `job_ftch/adapters/telegram_bot/Dockerfile.prod`;
- compose: `job_ftch/adapters/telegram_bot/docker-compose.prod.yml`;
- tenant configs: `job_ftch/adapters/telegram_bot/config/tenants/`;
- runtime overlay: `job_ftch/adapters/telegram_bot/runtime.prod.yaml`.

MCP, FastAPI, FastStream и Dagster adapters не входят в этот deploy.

## Требования

- Ubuntu 24.04 или совместимый Linux host.
- Docker и Docker Compose plugin.
- Минимум для текущих compose limits: 2 vCPU / 6 GB RAM.
- Заполненные `.env.prod` и `job_ftch/adapters/telegram_bot/.env.prod`.

## Подготовка

```bash
git clone https://github.com/letya999/job_ftch
cd job_ftch
cp .env.prod.example .env.prod
cp job_ftch/adapters/telegram_bot/.env.prod.example job_ftch/adapters/telegram_bot/.env.prod
```

Обязательные секреты:

- `POSTGRES_PASSWORD`;
- `JOB_FTCH_TELEGRAM_API_ID`;
- `JOB_FTCH_TELEGRAM_API_HASH`;
- `JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN`;
- `JOB_FTCH_OPENAI_API_KEY`;
- OpenObserve/Langfuse credentials, если включена observability.

Non-secret runtime policy не должна жить в `.env.prod`: она задаётся в
`config/runtime.yaml`, `config/runtime.prod.yaml` и bot runtime overlay.

## Проверка compose

```bash
docker build -f docker/runtime/Dockerfile.prod -t job-ftch-runtime:prod .
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml config
```

`docker compose config` должен падать, если `POSTGRES_PASSWORD` пустой.

## Запуск

```bash
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml up -d --build
```

Логи:

```bash
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml logs -f bot
```

## Что поднимается

- `bot` — Telegram bot + tenant scheduler.
- `postgres` — durable store, outbox, runtime state.
- `qdrant` — vector backend, если tenant/runtime включает hybrid/vector path.
- volumes для Postgres, Qdrant, bot runtime и HuggingFace/cache данных.

Готовность bot контейнера проверяется marker-файлом scheduler loop. Если
healthcheck stale, бот может отвечать на команды, но scheduled ingest считается
неисправным.

## Эксплуатация

Backup Postgres:

```bash
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
  exec postgres pg_dump -U job_user job_ftch > backup.sql
```

Update:

```bash
git pull
docker build -f docker/runtime/Dockerfile.prod -t job-ftch-runtime:prod .
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml up -d --build
```

Перед release используйте [release_checklist](release_checklist.md).
