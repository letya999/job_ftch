---
title: "Observability"
description: "Логи, метрики, traces, quality checks and runtime diagnostics for job_ftch."
updated: 2026-08-02
---
# Observability

Observability split is explicit: operational telemetry and ML/evaluation
signals are related, but owned by different runtime paths.

## Operational Telemetry

| Сигнал | Source of truth |
| ------ | --------------- |
| Structured logs | `structlog` wiring and runtime env |
| OpenTelemetry | `opentelemetry-*` dependencies and tracing settings |
| Langfuse | `docs/adr/043-langfuse-observability.md` |
| Compose env | `deploy/observability/.env*.example` |
| Runtime verification | `scripts/verify_observability_run.py` |

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
