---
title: "Видение job_ftch"
description: "Назначение, границы и текущее состояние проекта job_ftch."
updated: 2026-08-05
---
# Видение job_ftch

`job_ftch` — async pipeline для сбора вакансий из Telegram, карьерных сайтов,
RSS/API-источников и runtime-добавлений. Система приводит входящие сообщения и
страницы к каноническим `JobRecord`, удаляет дубли, принимает evidence-based
решение и группирует одинаковые вакансии в `JobGroup`.

## Для кого

- AI/tech-сообщества, которым нужен собственный агрегатор вакансий.
- Разработчики, которые хотят расширять источники, парсеры, sinks и runtime
  adapters без переписывания core.
- Data-команды, которым нужен структурированный поток вакансий для анализа.

## Что уже есть

- Production recipe для tenant `ai_jobs`: 17 live sources, 40 profile shots,
  pinned graph, eval gates и release metrics.
- Multi-tenant runtime через `TenantRunner`.
- Telegram bot как основной production-shape adapter.
- MCP, FastAPI, FastStream и Dagster adapters как дополнительные runtime
  поверхности.
- Source assessment, monitor/scraper/site-parser stack и adaptive bypass
  boundaries для career-site ingest.

## Что не является целью

- Это не универсальный crawler и не “скрапинг всего интернета”.
- Это не orchestrator/queue replacement.
- Это не монолитный scraper в одном файле.
- Это не realtime-first event streaming platform.
- Это не набор hardcoded правил под один сайт или один Telegram-канал.

## Текущий фокус

- Держать production graph воспроизводимым и проверяемым.
- Сохранять чистые layer boundaries: `domain`, `application`, `nodes`,
  `infrastructure`, `adapters`.
- Расширять ingest через registered adapters/plugins, а не через core dispatch.
- Документировать runtime truth рядом с кодом: recipes, graph reference, source
  stack, node/entity catalog.

## Куда смотреть дальше

- [Архитектура](architecture.md)
- [Quickstart](quickstart.md)
- [Production recipe](recipes/pipeline_recipe.md)
- [Source stack](sources/ingest_stack.md)
- [Runtime/env](adapters/runtime_and_env.md)
