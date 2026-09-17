---
title: "Observability"
description: "Логи, метрики, traces, quality checks and runtime diagnostics for job_ftch."
updated: 2026-09-17
---
# Observability

Observability split is explicit: operational telemetry and ML/evaluation
signals are related, but owned by different runtime paths.

## Operational Telemetry

| Сигнал | Source of truth |
| ------ | --------------- |
| Structured logs | `structlog` wiring and runtime env |
| OpenTelemetry | `opentelemetry-*` dependencies and tracing settings |
| OpenObserve logs and metrics | `job_ftch/infrastructure/observability/openobserve.py` |
| CAPTCHA encounters | log events `captcha_encounter` and `captcha_solve_outcome`; dashboard `job_ftch captcha` |
| Compose env | `deploy/observability/.env*.example` |
| Runtime verification | `scripts/verify_observability_run.py` |

## CAPTCHA telemetry

Every classified challenge emits `captcha_encounter` (host, type, surface, outcome=`observed`). Solver attempts emit `captcha_solve_outcome` with `captcha_solved` and `captcha_outcome` (`solved` / `failed` / `unsupported`). Fields are first-class OpenObserve columns (`captcha_host`, `captcha_type`, …). Dashboard `job_ftch captcha` graphs frequency by type, stacked encounters by host, and unsolved outcomes. Upserted on OpenObserve startup from `job_ftch/infrastructure/observability/dashboards/job_ftch_captcha.json`.

## Quality And Regression

Evaluation and graph promotion gates are documented in
[pipeline recipe](../recipes/pipeline_recipe.md) and ADRs:

- [069 split operational and ML observability](../adr/069-split-operational-and-ml-observability.md);
- [070 MVP run delivery and graph promotion contract](../adr/070-mvp-run-delivery-and-graph-promotion-contract.md);
- [071 durable delivery and runtime degradation](../adr/071-durable-delivery-and-runtime-degradation.md).

Для regression gates используйте команды из [CI/CD](ci-cd.md):

```powershell
just eval-filtering
just eval-publishing
```

`scripts/run_diagnostics.py` остаётся низкоуровневым инструментом расследования
конкретного runtime incident, а не quality gate. Его параметры и входные
артефакты нужно сверять с самим скриптом и [pipeline recipe](../recipes/pipeline_recipe.md).
