# Telegram bot adapter

The **MVP deployable**: `job_ftch` core and the Telegram bot ship together in a
single container (polling mode, no public HTTPS required). This is the runtime
referenced by the `bot` service in the repo-root [docker-compose.yml](../../docker-compose.yml).

## What it does

- `/search`, `/subscribe`, `/digest` over the collected job catalog.
- Runs the tenant pipeline scheduler in the background and pushes new matches.
- Optional resume upload and per-source management.

## Configuration

Set via environment (see repo-root [.env.example](../../.env.example)):

| Variable | Purpose |
|---|---|
| `JOB_FTCH_TELEGRAM_API_ID` / `JOB_FTCH_TELEGRAM_API_HASH` | Telegram MTProto credentials (https://my.telegram.org) |
| `JOB_FTCH_CONFIGS_DIR` | Tenant configs directory (default `config/tenants`) |
| `JOB_FTCH_STORE_BACKEND` | `sqlite` (default) / `postgres` |
| `JOB_FTCH_STORE_DSN` | DSN when using postgres |

Bot token and any LLM keys are resolved through `EnvAuthProvider`.

## Run locally

```bash
uv sync --extra telegram --extra sqlite --extra openai --extra feeds
JOB_FTCH_CONFIGS_DIR=config/tenants uv run job_ftch telegram-bot
```

## Run with Docker

```bash
docker build -f adapters/telegram_bot/Dockerfile -t job-ftch-bot .
docker run --env-file .env job-ftch-bot
```

## Deploy

The bot is the default service in compose; see [docs/deploy.md](../../docs/deploy.md)
for the DigitalOcean droplet guide.
