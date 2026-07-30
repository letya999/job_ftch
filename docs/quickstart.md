---
title: "Быстрый старт"
description: "Быстрый локальный запуск job_ftch на fixture tenant без внешних сервисов."
updated: 2026-07-28
---
# Быстрый старт

Этот сценарий проверяет, что CLI, tenant config, graph compilation и fixture
ingest работают локально. Telegram, OpenAI, Postgres и Qdrant не нужны.

## 1. Установка

```powershell
git clone https://github.com/letya999/job_ftch.git
cd job_ftch
uv sync
```

Требования: Python 3.12+ и `uv`.

## 2. Tenant config для smoke-запуска

`job_ftch run` принимает tenant YAML. Каталог
`docs/examples/sources.example.yaml` нельзя передавать в `run` напрямую: это
справочник source snippets, а не tenant config.

```powershell
New-Item -ItemType Directory -Force artifacts/smoke | Out-Null
@'
tenant_id: smoke
display_name: Smoke Test
sources:
  - type: local_fixture
    path: fixtures/e2e/multisource_positive.jsonl
    source_name: positive_fixture
output:
  backend: json_file
  path: artifacts/smoke/jobs.json
  jsonl: false
  schema_version: job_ftch.job_record.v1
'@ | Set-Content -Encoding utf8 artifacts/smoke/tenant.yaml
```

## 3. Проверка без запуска

```powershell
uv run job_ftch validate --config artifacts/smoke/tenant.yaml
```

Ожидаемый результат:

```text
OK: artifacts/smoke/tenant.yaml parses and produces a valid PipelineBuilder.
```

## 4. Запуск

```powershell
uv run job_ftch run --config artifacts/smoke/tenant.yaml --max-items 20 --json
```

Выходы:

- `artifacts/smoke/jobs.json` — accepted `JobRecord`, если текущий graph
  принял элементы.
- `artifacts/smoke/review.jsonl` — элементы для review.
- `artifacts/smoke/rejected.jsonl` — контролируемые rejected/drop records.
- `artifacts/smoke/quarantine.jsonl` — quarantine lane.

Пустой `jobs.json` не означает сломанный запуск: production graph может
отправить fixture slice в review/rejected/drop lanes.

## 5. Проверки для разработки

Быстрый docs/config sanity:

```powershell
uv run python scripts/lint_docs.py
uv run python scripts/check_config_layers.py
```

Eval gates:

```powershell
uv run python scripts/evaluate_classification.py --gate
uv run python scripts/evaluate_extraction.py --gate
```

Полные локальные группы см. в [release_checklist](release_checklist.md).

## 6. Дальше

- Реальные источники: [sources/setup](sources/setup.md).
- Полный ingest stack: [sources/ingest_stack](sources/ingest_stack.md).
- Production recipe: [recipes/pipeline_recipe](recipes/pipeline_recipe.md).
- Runtime/env truth: [adapters/runtime_and_env](adapters/runtime_and_env.md).
- Deploy: [deploy](deploy.md).

## Частые ошибки

| Симптом | Причина | Что сделать |
|---|---|---|
| `ModuleNotFoundError: job_ftch` | Команда запущена не через `uv run` или не из repo root | Запускать `uv run ...` из корня проекта |
| `INVALID` на validate | YAML не соответствует `TenantConfig` | Сверить с примером выше и [TenantConfig](entities/tenant_config.md) |
| Нет accepted jobs | Элементы ушли в review/rejected/drop | Смотреть summary JSON и side-channel файлы |
| 401/403 на реальных источниках | Нет credentials или нужен bypass | Проверить `.env.*`, source assessment и bypass docs |

## Новая профессия или новый профиль

Production recipe по умолчанию настроен под `ai_jobs` / AI-engineering профиль.
Если запускать pipeline под другую профессию, нельзя просто заменить sources и
название профиля: текущий `tfidf_logreg_prefilter` обучен под AI-engineering
и может отрезать релевантные вакансии до LLM judge.

Перед production-запуском другого профиля нужно:

1. Собрать profile shots: минимум 12 negative resume shots, 12 positive resume
   shots, 12 positive vacancy shots и 12 negative vacancy shots.
2. Собрать labelled dataset под эту профессию.
3. Разметить dataset на `relevant=1/0`.
4. Обучить отдельный prefilter artifact через
   `scripts/eval/train_relevance_prefilter.py`.
5. Прогнать hold-out/live eval и подтвердить P/R/F1.

До выполнения этих шагов запускайте новый профиль только с отключенным
`tfidf_logreg_prefilter` или в экспериментальном режиме без публикации.
