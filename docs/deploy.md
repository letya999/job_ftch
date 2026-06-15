# Deployment — DigitalOcean droplet

This guide deploys the MVP: `job_ftch` core + Telegram bot in one container,
on a single droplet, with Docker Compose. No Kubernetes required.

## 1. Provision

- Create an Ubuntu 24.04 droplet (1 vCPU / 2 GB is enough for the SQLite MVP;
  pick 2 vCPU / 4 GB if you enable Postgres + Qdrant).
- SSH in and install Docker + the compose plugin:

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Get the code

```bash
git clone https://github.com/<OWNER>/job_ftch
cd job_ftch
```

Fill `.env.dev` and `adapters/telegram_bot/.env.dev`:

- `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH` — from https://my.telegram.org
- `JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN` — from BotFather
- `JOB_FTCH_OPENAI_API_KEY` — for extraction and embeddings
- keep `JOB_FTCH_STORE_BACKEND=postgres`
- keep `JOB_FTCH_QDRANT_URL` enabled for vector search

## 3. Run (default: bot + Postgres + Qdrant)

```bash
docker compose up -d --build
docker compose logs -f bot
```

The bot polls Telegram and runs the tenant scheduler. State lives in Postgres,
vectors live in Qdrant, and `/app/.runtime` stores the Telethon session and local artifacts.

## 4. Operate

- **Logs:** `docker compose logs -f bot`
- **Update:** `git pull && docker compose up -d --build`
- **Backup (Postgres):** `docker compose exec postgres pg_dump -U job_user job_ftch > backup.sql`
- **Restart policy:** `bot` uses `restart: unless-stopped`, so it survives reboots.

## Other adapters

The MCP server, FastAPI, FastStream, and Dagster adapters live under
[adapters/](../adapters) with their own Dockerfiles and READMEs. They are not part
of the MVP deploy; deploy them separately when needed.
