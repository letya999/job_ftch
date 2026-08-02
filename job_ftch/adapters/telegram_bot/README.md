# Telegram bot adapter

The Telegram adapter is deployed as a thin image on top of a shared
`job_ftch` runtime image. The adapter keeps the Telegram entrypoint and bot
dependencies; pipeline, storage, embeddings, and browser capabilities live in
the shared runtime image.

## What it does

- On-demand pipeline execution to discover and filter jobs.
- Tenant scheduler execution with channel publishing.
- Private owner reports after every completed scheduled run.
- Resume upload and per-source management.

### Commands

- `/start` — Главное меню / Статус
- `/positive` — Добавить подходящее резюме
- `/negative` — Добавить НЕ подходящее резюме
- `/positive_job` — Добавить подходящую вакансию (пример)
- `/negative_job` — Добавить НЕ подходящую вакансию (пример)
- `/examples` — Список моих примеров
- `/sources` — Список источников (URL). Кнопки переключения листаются по 8 на страницу,
  текстовый список показывает все источники целиком.
- `/run` — Запустить поиск сейчас
- `/clear` — Очистить историю
- `/schedule` — Настроить частоту автозапуска и посмотреть последний scheduler status
- `/channel` — Настроить канал публикации вакансий
- `/feedback` — Обратная связь на опубликованные вакансии (только админ)

### Scheduled runs

The production bot process owns the tenant scheduler loop. A scheduled run uses
the configured publish owner's profile (`publish_user_id`) so it follows the
same profile-aware filtering as manual `/run`.

After every completed scheduled run, the bot sends a private report to the
publish owner chat, not to the public channel. The report reuses the shared
runtime report buckets used by `/run`:

- `Уже видели` — snapshot/dedup/already-seen drops, including duplicate content.
- `Не-вакансии` — explicit non-vacancy/content-policy drops.
- `Низкая релевантность` — relevance-prefilter drops.
- `Прочие дропы` — remaining controlled drops and operational source drops.

If a run emits zero jobs and there is no pending publish retry window, channel
publication is skipped deliberately. The scheduler persists that as:

- `bot_scheduler:last_publish_skipped_at`
- `bot_scheduler:last_publish_skipped_reason`

`/schedule` shows these fields so a successful no-output run does not look like
a silent publishing failure.

### Обратная связь на вакансии

Админ выбирает режим через `/feedback`:

- **Выключить** — карточки публикуются как раньше, без кнопки.
- **Только админы** — кнопка есть у всех, но отметку примут лишь от админа.
- **Все читатели** — отметить может любой читатель канала.

В двух последних режимах под каждой опубликованной карточкой появляется кнопка
«🚫 Не по профилю». Карточка одинакова для всех, кто видит канал, поэтому наличие кнопки
не может кодировать право: разрешение проверяется в момент нажатия.

- Одна отметка на пару (вакансия, читатель): повторное нажатие не увеличивает счётчик.
- Отметки копятся в run-state тенанта и агрегируются по вакансиям и источникам.
- `/feedback` показывает сводку и помечает ✅ те вакансии, которые отметили минимум два
  разных читателя.

Перенос в негативные примеры профиля — **ручной**: `/feedback` только называет готовые к
переносу вакансии, добавляет их админ через `/negative_job`. Это сделано намеренно.
Замер 2026-07-24 показал, что непроверенные негативные шоты снижают и точность, и полноту:
вакансии «не по профилю» лежат в векторном пространстве рядом с целевыми ролями, поэтому
негатив, поставленный рядом, тянет вниз и настоящие попадания.

Кнопка ничего не меняет в уже опубликованной вакансии — это сбор свидетельств, а не
управление выдачей.

## Configuration

The bot now uses three layers:

1. repo-root env for shared secrets/backends
2. adapter env for bot auth and adapter wiring
3. adapter runtime YAML for non-secret bot-specific tuning

Set via repo-root `.env.dev` plus adapter-specific `job_ftch/adapters/telegram_bot/.env.dev`:

| Variable | Purpose |
|---|---|
| `JOB_FTCH_TELEGRAM_API_ID` / `JOB_FTCH_TELEGRAM_API_HASH` | Telegram MTProto credentials (https://my.telegram.org) |
| `JOB_FTCH_CONFIGS_DIR` | Tenant configs directory (default `config/tenants`) |
| `JOB_FTCH_RUNTIME_CONFIG_PATH` | Runtime YAML chain for the bot process |
| `JOB_FTCH_STORE_BACKEND` | `postgres` for the Docker runtime |
| `JOB_FTCH_STORE_DSN` | DSN used by the library store/job/search backends |
| `JOB_FTCH_QDRANT_URL` | Qdrant endpoint for vector search |
| `JOB_FTCH_OPENAI_API_KEY` | OpenAI key for extraction and embeddings |
| `JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `JOB_FTCH_AUTH_TELEGRAM_BOT_*` allowlists | Admin/user/chat access gates and throttling |

Bot token and related auth controls are resolved through `EnvAuthProvider`.
Bot-specific non-secret overrides live in:

- `job_ftch/adapters/telegram_bot/runtime.dev.yaml`
- `job_ftch/adapters/telegram_bot/runtime.prod.yaml`

## Run locally

Build the shared runtime image first:

```bash
docker build -f docker/runtime/Dockerfile.dev -t job-ftch-runtime:dev .
```

```bash
docker compose -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml up -d --build
docker compose -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml logs -f bot
```

## Run with Docker

```bash
docker build -f docker/runtime/Dockerfile.prod -t job-ftch-runtime:prod .
docker build -f job_ftch/adapters/telegram_bot/Dockerfile.prod -t job-ftch-telegram-bot:prod .
```

## Deploy

The bot deployable lives in [docker-compose.prod.yml](docker-compose.prod.yml);
see [docs/deploy.md](../../docs/deploy.md)
for the DigitalOcean droplet guide.
