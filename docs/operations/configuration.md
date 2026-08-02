---
title: "Configuration"
description: "Операционная карта конфигурации job_ftch: env files, runtime YAML, tenant overlays and secrets."
updated: 2026-08-02
---
# Configuration

Конфигурация централизована в коде и YAML, а документация только указывает на
source of truth.

## Source Of Truth

| Слой | Файлы |
| ---- | ----- |
| Settings | `job_ftch/config.py` |
| Root runtime | `config/runtime.yaml`, `config/runtime.dev.yaml`, `config/runtime.prod.yaml` |
| Telegram bot runtime | `job_ftch/adapters/telegram_bot/runtime.dev.yaml`, `job_ftch/adapters/telegram_bot/runtime.prod.yaml` |
| Tenants | `job_ftch/adapters/telegram_bot/config/tenants/*.yaml` |
| Env examples | `.env*.example`, `job_ftch/adapters/telegram_bot/.env*.example`, `deploy/observability/.env*.example` |

## Rules

- Секреты живут только в env/secret manager, не в YAML, fixtures или docs.
- Runtime policy живёт в `config/runtime*.yaml` и bot runtime overlays.
- Tenant-level sources и wiring живут в tenant YAML.
- Границы env/runtime/tenant проверяет `scripts/check_config_layers.py`.

Подробный навигатор: [runtime_and_env](../adapters/runtime_and_env.md).
