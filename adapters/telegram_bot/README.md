# Telegram bot adapter

The **MVP deployable**: `job_ftch` core and the Telegram bot ship together in a
single container (polling mode, no public HTTPS required). This is the runtime
referenced by the `bot` service in the repo-root [docker-compose.yml](../../docker-compose.yml).

## What it does

- `/search`, `/subscribe`, `/digest` over the collected job catalog.
- Runs the tenant pipeline scheduler in the background and pushes new matches.
- Optional resume upload and per-source management.

## Configuration

Set via the repo-root `.env.dev` and adapter-specific `adapters/telegram_bot/.env.dev`:

| Variable | Purpose |
|---|---|
| `JOB_FTCH_TELEGRAM_API_ID` / `JOB_FTCH_TELEGRAM_API_HASH` | Telegram MTProto credentials (https://my.telegram.org) |
| `JOB_FTCH_CONFIGS_DIR` | Tenant configs directory (default `config/tenants`) |
| `JOB_FTCH_STORE_BACKEND` | `postgres` for the Docker runtime |
| `JOB_FTCH_STORE_DSN` | DSN used by the library store/job/search backends |
| `JOB_FTCH_QDRANT_URL` | Qdrant endpoint for vector search |
| `JOB_FTCH_OPENAI_API_KEY` | OpenAI key for extraction and embeddings |

Bot token and any LLM keys are resolved through `EnvAuthProvider`.

## Run locally

```bash
docker compose up -d --build
docker compose logs -f bot
```

## Run with Docker

```bash
docker build -f adapters/telegram_bot/Dockerfile -t job-ftch-bot .
docker run --env-file .env.dev --env-file adapters/telegram_bot/.env.dev job-ftch-bot
```

## Deploy

The bot is the default service in compose; see [docs/deploy.md](../../docs/deploy.md)
for the DigitalOcean droplet guide.
