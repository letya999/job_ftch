---
title: "Telegram Bot Deploy"
description: "- Use polling mode by leaving `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL` unset."
updated: 2026-07-28
---
# Telegram Bot Deploy

## Local development

- Use polling mode by leaving `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL` unset.
- Keep non-secret bot policy in `job_ftch/adapters/telegram_bot/runtime.dev.yaml`.
- Fill `.env.dev` and `job_ftch/adapters/telegram_bot/.env.dev`.
- Start the bot with tenant configs:

```bash
uv run job_ftch telegram-bot --configs-dir job_ftch/adapters/telegram_bot/config/tenants
```

## Production webhook

- Set `JOB_FTCH_AUTH_TELEGRAM_BOT_SECRET_TOKEN`
- Set `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL`
- Keep non-secret bot policy in `job_ftch/adapters/telegram_bot/runtime.prod.yaml`
- Expose `POST /webhook/telegram` behind HTTPS

## Docker Compose

Production stack: `job_ftch/adapters/telegram_bot/docker-compose.prod.yml` (bot, PostgreSQL,
Qdrant).

Copy `.env.prod.example` to `.env.prod` next to the compose file and fill it in. Three of
its variables are read by compose itself rather than by the application:

| variable | default | notes |
|---|---|---|
| `POSTGRES_USER` | `job_user` | |
| `POSTGRES_DB` | `job_ftch` | |
| `POSTGRES_PASSWORD` | **none** | compose refuses to start without it |

`POSTGRES_PASSWORD` deliberately has no fallback, so a stack cannot come up on a guessable
password. Omitting it fails immediately with
`required variable POSTGRES_PASSWORD is missing a value`.

`--env-file` is required. `env_file:` inside the compose file only injects variables into
the container; the `${POSTGRES_PASSWORD}` substitution in the compose file itself is
resolved before that, from the shell or the file given here. Without it the stack fails
with the missing-variable error above even though `.env.prod` defines the value.

```bash
cd job_ftch/adapters/telegram_bot
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

The bot container reports `healthy` only after polling actually starts (see the readiness
marker in `main.py`), so `docker compose ps` showing `healthy` means the bot answers, not
merely that the process exists. First start takes a few minutes: it loads tenants, merges
the ontology and warms the embedding model.
