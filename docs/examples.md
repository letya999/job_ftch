---
title: "Примеры"
description: "Минимальные исполняемые примеры: tenant на fixture, validate, run и eval gate."
updated: 2026-07-28
---
# Примеры

## Tenant на fixture

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

Проверить конфиг без ingest:

```powershell
uv run job_ftch validate --config artifacts/smoke/tenant.yaml
```

Запустить pipeline на fixture:

```powershell
uv run job_ftch run --config artifacts/smoke/tenant.yaml --max-items 20 --json
```

Артефакты пишутся в `artifacts/smoke/`: `jobs.json`, `review.jsonl`,
`rejected.jsonl`, `quarantine.jsonl`. `jobs.json` может быть пустым, если
текущий graph отправил элементы в review/rejected/drop lanes.

## Eval gates

```powershell
uv run python scripts/evaluate_classification.py --gate
uv run python scripts/evaluate_extraction.py --gate
```

Полный production recipe и release команды: [pipeline_recipe](recipes/pipeline_recipe.md).

## Примеры выходных файлов

- `fixtures/examples/job_output.json`
- `fixtures/examples/rejected_output.jsonl`
