---
title: "job_ftch_site API"
description: "Публичный read-only API витрины, Telegram bridge и правила fair use."
updated: 2026-08-24
---

# job_ftch_site API

Сайт читает live runtime tenant `ai_jobs` через Telegram bridge. Источник
истины — тот же `TenantRunner` и DB, которые используют бот и pipeline.

## Endpoints

```text
GET /public/tenants/ai_jobs/sources.json
GET /public/tenants/ai_jobs/jobs.json?limit=1000
```

Оба endpoint public-safe, read-only и не требуют bridge API key. Публикуются
только allowlisted tenant и санитизированные поля. Query/fragment с runtime
URL, credentials, private Telegram entities, raw specs и debug metadata не
попадают наружу.

Сайт проксирует эти endpoints через свои:

```text
GET /api/v1/sources
GET /api/v1/jobs?limit=1000
GET /api/health
```

Ответы сайта кэшируются на 15 секунд; bridge кэширует runtime registry и
ограничивает public reads через slowapi. При нескольких replicas process-local
cache/limiter следует вынести в Redis или edge/API gateway.

Реестр вакансий полностью читается из PostgreSQL tenant job/group storage.
Telegram используется только как выходной delivery adapter и никогда не
сканируется публичным API.

## Telegram bot

Для локального bridge сначала заполни `job_ftch/adapters/telegram_bot/.env.dev`
или `.env.prod` из соответствующего `.env.*.example`; compose требует отдельный
`JOB_FTCH_AUTH_TELEGRAM_BOT_SECRET_TOKEN` и не запускает webhook без него.

- `/sources` — текущие источники и health;
- `/published` — последние вакансии, подтверждённые publish ledger;
- `/run` — запуск pipeline остаётся защищённым bot auth и не доступен public API.

Bot token и bridge API key в сайт не передаются. `JOB_FTCH_PUBLIC_API_TOKEN`
может использоваться только server-side для закрытого upstream, если это
понадобится в конкретном deploy.

## Legal surface

Сайт публикует:

- `/legal/privacy`;
- `/legal/cookies`;
- `/legal/licensing`;
- `/legal/disclaimer`;
- `/legal/terms`;
- `/legal/fair-use`;
- `/llms.txt` и `/llms-full.txt`.

Analytics scripts загружаются только после consent, сохранённого в
`localStorage` под ключом `job_ftch_consent`.
