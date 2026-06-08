# ADR-021: Module Boundary Enforcement

- Status: ACCEPTED
- Date: 2026-06-08

## Context

После перехода на пакет `job_ftch/` проекту нужен повторяемый gate, который не даст
слоям `domain/`, `application/` и `nodes/` постепенно протечь в инфраструктуру или
runtime-адаптеры.

## Decision

- Вводим `scripts/check_module_boundaries.py`.
- Проверка анализирует AST-импорты и валидирует:
  - `job_ftch.domain` импортирует только stdlib, `pydantic` и собственные `job_ftch.domain.*`.
  - `job_ftch.application` импортирует только stdlib, `pydantic`, `job_ftch.domain.*`,
    `job_ftch.application.*`.
  - `job_ftch.nodes` не импортирует `job_ftch.infrastructure.*` и `job_ftch.adapters.*`.
- Скрипт запускается в CI и через `.pre-commit-config.yaml`.

## Consequences

- Механические namespace-рефакторинги перестают быть одноразовой акцией и становятся
  автоматически контролируемым контрактом.
- Новые runtime-адаптеры остаются вне core-слоев.
