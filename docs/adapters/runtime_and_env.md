---
title: "Runtime и env: где правда"
description: "Короткая карта того, какие файлы являются source of truth для конфигурации и переменных окружения."
updated: 2026-07-28
---
# Runtime и env: где правда

Отдельные длинные документы про `configuration` и `env_reference` удалены,
потому что они слишком легко начинали дублировать реальные файлы.

Здесь оставлен только короткий навигатор по источникам правды.

## Где смотреть конфигурацию

- `job_ftch/config.py` — полный набор `Settings`, дефолты, валидация и порядок источников.
- `config/runtime.yaml` — общая runtime-база.
- `config/runtime.dev.yaml` — dev-override.
- `config/runtime.prod.yaml` — prod-override.
- `job_ftch/adapters/telegram_bot/runtime.dev.yaml` — bot-специфичный dev runtime.
- `job_ftch/adapters/telegram_bot/runtime.prod.yaml` — bot-специфичный prod runtime.
- `job_ftch/adapters/telegram_bot/config/tenants/*.yaml` — tenant-level sources и wiring.
- `scripts/check_config_layers.py` — машинная проверка границ между tenant и runtime слоями.

## Где смотреть env

- `.env.dev.example`
- `.env.prod.example`
- `job_ftch/adapters/telegram_bot/.env.dev.example`
- `job_ftch/adapters/telegram_bot/.env.prod.example`
- `deploy/observability/.env.dev.example`
- `deploy/observability/.env.prod.example`

Это и есть актуальные шаблоны переменных окружения.

## Порядок применения

`Settings` читает `.env` плюс `.env.dev` или `.env.prod` в зависимости от
`JOB_FTCH_ENV`; реальные переменные окружения имеют приоритет над dotenv.
Runtime YAML читается через `JOB_FTCH_RUNTIME_CONFIG_PATH`; compose для bot
задаёт цепочку `config/runtime.yaml`, env-specific root runtime и
`job_ftch/adapters/telegram_bot/runtime*.yaml`.

## Residential proxy rescue

Residential proxies are a paid rescue tier, not the default route for every
career site. The root `.env*.example` files contain a DataImpulse RU gateway
profile (`gw.dataimpulse.com:823`) with 1 GB total per-process budget,
0.05 GB per-domain budget, allow-domains for Habr/Higgsfield/EPAM rescue sources, and
deny-domains for banking/government targets. Real `JOB_FTCH_PROXY_USER` and
`JOB_FTCH_PROXY_PASS` values must live only in local/compose env files.

## Практическое правило

- Секреты, токены, DSN и URL ищите в `.env*.example`.
- Политику пайплайна, модели, пороги, concurrency и budgets ищите в `config/runtime*.yaml`.
- Bot-specific auth, tenant directory и compose wiring ищите в
  `job_ftch/adapters/telegram_bot/*`.
- Operational telemetry env лежит отдельно в `deploy/observability/*`.
- Что именно существует и как резолвится, проверяйте по `job_ftch/config.py`, а не по prose.
