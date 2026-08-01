---
title: "Релизный чеклист"
description: "Исполняемый релизный чеклист: локальные gates, docs/config parity, production recipe и deploy contract."
updated: 2026-08-01
---
# Релизный чеклист

Канонический набор локальных gate-ов задан в `scripts/run_ci_checks.py`.
Документ ниже не должен расходиться со скриптом.

## 1. Предварительная проверка

```powershell
git status --short
ai-repo-safety scan --target .
```

Проверить вручную:

- в diff нет секретов, локальных сессий, cache/runtime artifacts;
- production recipe: `config/recipes/production_pipeline_recipe.yaml`;
- production runtime: `config/runtime.prod.yaml`;
- production graph: `config/pipelines/evidence_v2_compact_prefilter.yaml`;
- hash в `config/runtime.prod.yaml` совпадает с recipe.
- live tenant `job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml`
  содержит те же 17 source ids, что и `fixtures/sources/ai_jobs.json`.

## 2. Локальные gates

```powershell
uv run python scripts/run_ci_checks.py lint
uv run python scripts/run_ci_checks.py type
uv run python scripts/run_ci_checks.py test
uv run python scripts/run_ci_checks.py security
uv run python scripts/run_ci_checks.py core-import
uv run python scripts/run_ci_checks.py release-contract
```

Состав групп:

- `lint`: `ruff check`, module boundaries, docs lint, YAML validation,
  config-layer check, `ruff format --check`.
- `type`: `mypy job_ftch`.
- `test`: `tests` и `job_ftch/adapters/telegram_bot/tests`, кроме `network`,
  coverage floor `70`.
- `security`: `bandit` и `pip-audit` с текущим allowlist.
- `core-import`: импорт package, contracts и CLI.
- `release-contract`: graph/runtime/eval/source outcome contracts.

## 3. Документация

```powershell
uv run python scripts/build_index_docs.py
uv run python scripts/check_docs_generated.py
uv run python scripts/lint_docs.py
```

Если менялись graph manifests или registered node manifests:

```powershell
uv run python scripts/build_graph_reference.py
uv run python scripts/check_docs_generated.py
```

Если менялись config/env/runtime слои, обновить:

- `docs/adapters/runtime_and_env.md`;
- `docs/quickstart.md`;
- `docs/deploy.md`;
- `docs/release_checklist.md`.

## 4. Паритет config/env

Пары, которые должны оставаться согласованными:

- `.env.dev.example` / `.env.prod.example`;
- `job_ftch/adapters/telegram_bot/.env.dev.example` /
  `job_ftch/adapters/telegram_bot/.env.prod.example`;
- `deploy/observability/.env.dev.example` /
  `deploy/observability/.env.prod.example`;
- `config/runtime.yaml`, `config/runtime.dev.yaml`,
  `config/runtime.prod.yaml`;
- `job_ftch/adapters/telegram_bot/runtime.dev.yaml`,
  `job_ftch/adapters/telegram_bot/runtime.prod.yaml`.

Текущий production recipe требует:

- `embedding_enabled: false`;
- `bgem3_enabled: false`;
- `relevance_backend: keywords`;
- `openai_model: gpt-5.4-nano`;
- `relevance_llm_model: gpt-4.1-mini`;
- `pipeline_graph_path: config/pipelines/evidence_v2_compact_prefilter.yaml`.

Env examples не должны случайно переопределять эти значения.

Machine checks:

```powershell
uv run python scripts/check_config_layers.py
uv run python scripts/validate_yaml_schemas.py `
  config/runtime.prod.yaml `
  config/pipelines/evidence_v2_compact_prefilter.yaml `
  job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml
```

## 5. Проверки pipeline и sources

Минимум:

```powershell
uv run python scripts/evaluate_classification.py --gate
uv run python scripts/evaluate_extraction.py --gate
uv run python scripts/run_ci_checks.py release-contract
```

Career-site ingest coverage gate:

```powershell
uv run python scripts/run_ingest_batch.py `
  --input fixtures/sources/career_sites_cis_303.yaml `
  --out-json .runtime/runs/ingest_batch_303_direct_urls.json `
  --resume `
  --timeout 120 `
  --hard-cancel-grace 15 `
  --max-items 1 `
  --concurrency 10 `
  --gate `
  --min-success-rate 0.65
```

Для live release candidate:

- выполнить команды из [pipeline_recipe](recipes/pipeline_recipe.md);
- перед live `/run` выполнить `run_bot_ingest.py --preflight`;
- сохранить artifacts в `artifacts/release/`;
- для controlled eval подтвердить `comparison_key == 996a6833a6e90daa`
  и metric floors `P>=0.8 / R>=0.7 / F1>=0.75`;
- проверить один Telegram source и один allowed career-site source;
- подтвердить, что `SourceAssessmentAdapter` соответствует
  [source_assessment](sources/source_assessment.md).

## 6. Deploy contract

Production-shape deploy:

- `docker/runtime/Dockerfile.prod`;
- `job_ftch/adapters/telegram_bot/Dockerfile.prod`;
- `job_ftch/adapters/telegram_bot/docker-compose.prod.yml`;
- `docs/deploy.md`.

Проверка compose:

```powershell
docker build -f docker/runtime/Dockerfile.prod -t job-ftch-runtime:prod .
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod `
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml config
```

Запуск только после заполнения секретов:

```powershell
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod `
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml up -d --build
```

Перед push:

```powershell
ai-repo-safety prepush --target .
```

## 7. Заметки к релизу

Указать:

- production graph path и hash;
- extraction/relevance backend;
- source coverage и known partial/failing sources;
- eval artifact paths и metric floors;
- изменения docs/config/env/runtime precedence.
