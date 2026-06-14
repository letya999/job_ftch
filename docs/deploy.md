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
cp .env.example .env
```

Fill `.env`:

- `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH` — from https://my.telegram.org
- the Telegram bot token and any LLM API key (resolved via `EnvAuthProvider`)
- leave `JOB_FTCH_STORE_BACKEND=sqlite` for the zero-infra default

## 3. Run (default: bot + SQLite)

```bash
docker compose up -d --build
docker compose logs -f bot
```

The bot polls Telegram and runs the tenant scheduler. SQLite state lives in the
`botdata` volume (`/app/.runtime`).

## 4. Scale up (optional)

Postgres and Qdrant are behind compose profiles:

```bash
# add Postgres (set JOB_FTCH_STORE_BACKEND=postgres and JOB_FTCH_STORE_DSN in .env)
docker compose --profile postgres up -d --build

# add semantic search with Qdrant
docker compose --profile postgres --profile vector up -d --build
```

With Postgres, point the DSN at the service hostname:
`JOB_FTCH_STORE_DSN=postgresql://job_user:job_password@postgres:5432/job_ftch`.

## 5. Operate

- **Logs:** `docker compose logs -f bot`
- **Update:** `git pull && docker compose up -d --build`
- **Backup (SQLite):** copy the `botdata` volume, e.g.
  `docker run --rm -v job_ftch_botdata:/data -v "$PWD:/backup" alpine tar czf /backup/runtime.tgz /data`
- **Backup (Postgres):** `docker compose exec postgres pg_dump -U job_user job_ftch > backup.sql`
- **Restart policy:** `bot` uses `restart: unless-stopped`, so it survives reboots.

## Other adapters

The MCP server, FastAPI, FastStream, and Dagster adapters live under
[adapters/](../adapters) with their own Dockerfiles and READMEs. They are not part
of the MVP deploy; deploy them separately when needed.
