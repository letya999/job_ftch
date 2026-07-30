---
title: "Рецепт production-пайплайна"
description: "Зафиксированный рецепт ai_jobs: тестовый пользователь, тенант, источники, датасет, граф, настройки, метрики и регрессионные проверки."
updated: 2026-07-29
---
# Рецепт production-пайплайна

Машинный источник правды: `config/recipes/production_pipeline_recipe.yaml`.
Этот документ объясняет, как воспроизвести тот же рецепт руками и как понять,
что прогон сравним с текущим champion.

## Что зафиксировано

| Часть | Путь / значение |
|---|---|
| Тенант | `ai_jobs` |
| Тестовый пользователь | `fixtures/bootstrap/test_user.json` |
| OSS tenant fixture | `fixtures/bootstrap/tenant_ai_jobs.yaml` |
| Live tenant config | `job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml` |
| Источники | `fixtures/sources/ai_jobs.json`, 22 источника |
| Датасет controlled eval | `fixtures/dataset/eval_dataset_fixed140_locked_v1.jsonl` |
| Датасет обучения prefilter | `fixtures/dataset/eval_dataset.jsonl` |
| Граф | `config/pipelines/evidence_v2_compact_prefilter.yaml` |
| Graph hash | `0d73de0663d220da62e37d9a41159542547d167f9f096088f7ae85ec587e44fb` |
| Runtime base/prod | `config/runtime.yaml`, `config/runtime.prod.yaml` |
| Модели | extraction `gpt-5.4-nano`, relevance `gpt-4.1-mini` |
| Profile shots | 40: 10 positive resume, 10 negative resume, 10 positive jobs, 10 negative jobs |
| Prefilter | `fixtures/prefilter/tfidf_logreg_v1.json`, production threshold `0.20`, trained for `ai_jobs` / AI-engineering profile |

Для open source воспроизводимости используйте fixture-пару
`fixtures/bootstrap/test_user.json` + `fixtures/bootstrap/tenant_ai_jobs.yaml`.
Она указывает на тестового пользователя и явно содержит те же 22 источника,
что и `fixtures/sources/ai_jobs.json`. Live tenant config тоже содержит эти
22 источника, чтобы production `/run` был наполняемым сразу после заполнения
секретов и профиля пользователя.

## Как воспроизвести controlled eval

Controlled eval нужен для проверки изменений графа, промптов, ontology projection,
prefilter, моделей, сборки runtime и storage-поведения. На postgres reset
обязателен, иначе `jf_source_snapshots` и processed keys дают неполный candidate
set.

```powershell
uv run python scripts/eval/run_pipeline_eval.py `
  --runtime prod `
  --profile-source tenant `
  --tenant-config job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml `
  --tenant-id ai_jobs `
  --state-mode production `
  --reset-before-run `
  --graph config/pipelines/evidence_v2_compact_prefilter.yaml `
  --sample 400 `
  --seed 42 `
  --out artifacts/release/controlled_postgres_eval_400_YYYYMMDD.json
```

Обязательные признаки сравнимого прогона:

- `provenance.reset.performed == true`
- `provenance.reset.source_snapshots_after_reset == 0`
- `provenance.dirty_state == false`
- `provenance.candidate_counts.total_candidates == 486`
- `provenance.incomplete_candidate_set == false`
- `provenance.comparison_key == 996a6833a6e90daa`

Champion-метрики для этого рецепта:

| Метрика | Значение |
|---|---:|
| Precision | `0.8461538462` |
| Recall | `0.8148148148` |
| F1 | `0.8301886792` |
| LLM calls | `104` |
| Cost | `$0.0774684` |

Минимальный regression gate ниже champion-метрик и нужен как стоп-кран для
явных поломок, а не как требование повторять historical best:

| Gate | Floor |
|---|---:|
| Precision | `>= 0.8` |
| Recall | `>= 0.7` |
| F1 | `>= 0.75` |

## Decision policy notes

Graph `2.6.0` keeps the prefilter as a hard cost gate and lowers its
production threshold from `0.25` to `0.20`. Scores below `0.20` still do not
reach the LLM relevance judge.

The compact relevance decision also has a recall fallback for broad profiles:
an `adjacent` / `unknown` result may become ACCEPT only when the item is a job,
has cited positive evidence, has no cited negative evidence, and its vacancy
text contains a profile-specific target-role signal. Generic role words such
as manager/lead/engineer/developer/architect/analyst are currently suppressed
in code so a bare generic title cannot be promoted by this fallback.

That generic-token suppression is an explicit technical debt item, not the
desired long-term architecture. See `TD-030` in `docs/techdebt.md`; the
intended destination is a versioned ontology/profile artifact or graph
parameter.

После валидного controlled eval обновите tracked snapshot:

```powershell
uv run python scripts/eval/promote_champion_recipe.py `
  artifacts/release/controlled_postgres_eval_400_YYYYMMDD.json
```

Скрипт пишет только компактные regression-артефакты:
`config/recipes/champion.yaml` и `config/recipes/champion_artifact.json`.
Полный eval JSON остается в ignored `artifacts/`.

## Как воспроизвести `/run`

Эта команда повторяет Telegram `/run` через `TenantRunner`, но не отправляет
сообщения в Telegram. Она отвечает на вопрос “что даст production на живых
22 источниках сейчас?”.

Перед первым live-запуском проверьте, что tenant, runtime, storage и профиль
готовы:

```powershell
uv run python scripts/run_bot_ingest.py `
  --tenant ai_jobs `
  --runtime prod `
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants `
  --preflight
```

```powershell
uv run python scripts/run_bot_ingest.py `
  --tenant ai_jobs `
  --runtime prod `
  --configs-dir job_ftch/adapters/telegram_bot/config/tenants `
  --report-path artifacts/release/prod_run_live_22_clean_YYYYMMDD.json
```

Для recipe-замера не передавайте `--no-clean`: скрипт должен очистить run data
перед запуском.

В `prod`/`dev` runtime подготовка источников намеренно сериализована
(`source_preparation_concurrency=1`): на локальном Docker Postgres параллельная
подготовка live source set давала transient `asyncpg` connect timeout до fetch.
Fetch и pipeline-item concurrency остаются параллельными.

Зафиксированный live snapshot от `2026-07-26` хранится в
`config/recipes/live_run_terminal_artifact.json`. Его ручная разметка покрывает
только terminal decision set: `ACCEPT + REVIEW + REJECT`.

`tfidf_logreg_prefilter` в этом рецепте — hard gate, обученный под текущий
`ai_jobs` / AI-engineering профиль. При onboarding нового tenant/profile этот
узел нужно либо отключить, либо переобучить отдельный profile-specific artifact
и подтвердить recall на hold-out/live разметке до production-использования.
Минимум для нового профиля: 12 negative resume shots, 12 positive resume shots,
12 positive vacancy shots и 12 negative vacancy shots, плюс отдельный
размеченный dataset и обученный prefilter artifact под эту профессию.

| Метрика terminal set | Значение |
|---|---:|
| TP / TN / FP / FN | `10 / 17 / 0 / 4` |
| Precision | `1.0` |
| Recall | `0.7142857143` |
| F1 | `0.8333333333` |
| Wall latency | `162.666407s` |
| LLM usage requests | `48` |
| LLM relevance calls | `34` |
| Cost | `$0.05497551` |

Это не полный live recall по всем `763` sanitized items. Для полного end-to-end
recall нужно отдельно разметить prefilter/dedup/sanitize drops.

## Как сравнивать прогоны

`run_pipeline_eval.py` пишет `comparison_key` из graph hash, state mode,
backend, dataset hash, sample и seed. Если ключи различаются, `--compare-to`
печатает `NOT COMPARABLE` и не считает дельты.

```powershell
uv run python scripts/eval/run_pipeline_eval.py `
  --out artifacts/release/current.json `
  --compare-to artifacts/release/baseline.json
```

Ledger по локальным eval artifact:

```powershell
uv run python scripts/eval/build_recipe_ledger.py
```

Сгенерированный отчет: `docs/recipes/ledger.md`. Прогоны без provenance
попадают в non-reproducible и не должны становиться champion.

## Что проверяет регрессия

```powershell
uv run pytest tests/eval/test_champion_recipe.py tests/eval/test_production_recipe.py -q
uv run python scripts/check_config_layers.py
uv run python scripts/check_docs_generated.py
uv run python scripts/check_module_boundaries.py
uv run python scripts/run_ci_checks.py release-contract
```

Эти проверки падают, если без явного обновления recipe изменились graph/hash,
runtime flags, модели, postgres backend, prefilter artifact, source fixture,
controlled dataset hash, 40 shots, champion metrics или tracked live metrics.
