---
title: "RuntimeAdapter (Рантайм-адаптеры)"
description: "Runtime adapter — это внешний слой, который подключает library-first core"
updated: 2026-07-24
---
# RuntimeAdapter (Рантайм-адаптеры)

## Что это

Runtime adapter — это внешний слой, который подключает library-first core
`job_ftch` к конкретному окружению: CLI, Telegram bot, MCP, HTTP bridge или
worker wrapper.

В актуальной архитектуре важно различать три роли:

- port adapters реализуют application ports и живут в `infrastructure/` или `sinks/`
- assessment adapters оценивают source до ingest
- runtime adapters дают внешний вход в runtime и оркестрируют use cases

Runtime adapter не должен владеть pipeline policy. Его задача — принять
внешний запрос, собрать или выбрать tenant/runtime context и вызвать публичные
API вроде `PipelineBuilder`, `configure(...)` или `TenantRunner`.

## Зачем это нужно

Такое разделение оставляет ядро независимым от фреймворков и позволяет
использовать один и тот же core:

- как CLI и Python library;
- как Telegram bot;
- как MCP server;
- как вторичный HTTP bridge;
- как wrapper вокруг worker/orchestrator tooling.

## Главное правило изоляции

Если adapter обслуживает несколько независимых запросов, он не должен
переиспользовать один и тот же мутируемый builder instance без клонирования.

Правильный паттерн:

```python
async def run_pipeline(builder: PipelineBuilder, source_spec: SourceSpec) -> dict:
    pipeline = builder.clone().sources([source_spec]).build()
    summary = await pipeline.run_async()
    return summary.as_dict()
```

Без `clone()` адаптер начинает накапливать чужие `SourceSpec` и runtime state.

## Текущие adapter surfaces

| Adapter | Назначение | Текущее состояние |
|---|---|---|
| CLI / library path | `uv run job_ftch ...`, `configure(...)`, `PipelineBuilder` | Основной и наиболее зрелый путь |
| Telegram bot | операторский UX поверх tenant runtime | Активный runtime surface |
| MCP server | agent-facing control plane | Активный runtime surface |
| FastAPI bridge | дополнительный HTTP вход, включая webhook-oriented flows | Вторичный bridge, не основной production shape |
| FastStream wrapper | message-broker / worker integration | Вторичный wrapper, требует отдельного hardening |
| Dagster wrapper | orchestration / asset integration | Вторичный wrapper, требует отдельного hardening |

Это важно для релизной документации: наличие adapter surface не означает, что
он равен по зрелости основному CLI/tenant-runtime пути.

## Что adapter не должен делать

- тянуть business logic в слой framework handlers;
- принимать архитектурные решения вместо builder/runtime policy;
- импортировать внутренние infra-клиенты в обход публичного runtime API без
  явной необходимости;
- объявлять себя plugin через внутренние registry decorators.

## Связи с другими сущностями

- [PipelineBuilder](pipeline_builder.md) — основной composition-facing API
- [TenantConfig](tenant_config.md) — то, что adapter обычно получает или выбирает
- [SourceSpec](source_spec.md) — часто приходит в adapter извне
- [Plugin](plugin.md) — runtime adapters не являются plugins
