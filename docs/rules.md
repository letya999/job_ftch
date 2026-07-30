---
title: "Правила разработки"
description: "Короткие рабочие правила для изменений в job_ftch: границы слоёв, зависимости, тесты, документация и release gates."
updated: 2026-07-28
---
# Правила разработки

## Перед изменением

- Прочитать [architecture](architecture.md), если меняется runtime, pipeline,
  source stack или boundaries.
- Прочитать [tech_stack](tech_stack.md), если добавляется зависимость.
- Прочитать [ADR](adr/README.md), если меняется архитектурное решение.
- Для production graph свериться с [pipeline_recipe](recipes/pipeline_recipe.md).

## Правила слоёв

- `domain/`: только stdlib и `pydantic`.
- `application/`: orchestration/ports/composition; исключения контролирует
  `scripts/check_module_boundaries.py`.
- `nodes/`: processing stages; без прямого `infrastructure`/`adapters`.
- `infrastructure/`: внешние клиенты и реализации портов.
- `adapters/`: внешние runtime entrypoints.

Проверка:

```powershell
uv run python scripts/check_module_boundaries.py
```

## Правила pipeline

- `SanitizeNode` всегда первый.
- `SnapshotFilterNode`, если включён, всегда второй.
- Type changes только через `Stage[In, Out]`.
- `EvidenceDecisionNode` — единственный terminal decision owner.
- Sinks получают `JobRecord`, а не `JobDraft`.
- Post-accept enrichment не меняет terminal decision.

## Правила расширения

- Новый source/parser/sink/store/backend регистрируется через `register_*` или
  entry point.
- Не добавлять host-specific switches в `config.py` или core builder.
- Credentials не хранятся в YAML; только env/secret manager + `AuthProvider`.
- Тяжёлые зависимости — только в extras.

## Правила документации

- Каждый Markdown-файл имеет front matter: `title`, `description`, `updated`.
- После добавления/переезда/удаления docs запускать:

```powershell
uv run python scripts/build_index_docs.py
uv run python scripts/lint_docs.py
```

- Generated docs не править руками; использовать generator scripts.
- Runtime/env изменения отражать в [runtime_and_env](adapters/runtime_and_env.md).

## Цикл тестирования

Для локальной итерации:

```powershell
uv run pytest tests/test_<module>.py -q -o addopts="" --tb=line
```

Перед release использовать [release_checklist](release_checklist.md). Не
запускать весь suite verbose в foreground в агентном цикле: вывод слишком
шумный и не даёт лучшей диагностики.

## Запрещено

- Секреты в коде, YAML, fixtures или docs.
- Новые core `if/elif` dispatch tables для plugin/backend выбора.
- SQLAlchemy/ORM в stores.
- Kafka/Celery/Airflow/LangChain/LangGraph/Scrapy без нового ADR и явной смены
  tech stack policy.
- Смешивать source assessment, scraping и relevance decision в одном слое.
