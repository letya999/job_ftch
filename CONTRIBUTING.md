# Contributing to job_ftch

## Branches
- `main` — production-ready, protected. Direct pushes disabled.
- `dev` — integration branch, open for PRs from contributors.
- Feature branches: `feat/short-description` (from dev)
- Bugfix branches: `fix/short-description` (from dev)
- Docs branches: `docs/short-description` (from dev)
- Hotfix branches: `hotfix/short-description` (from main, rare)

## Pull Requests
- All PRs target `dev` branch (NOT main)
- `dev` → `main` merges only by repo maintainer after review
- PR title format: `type(scope): short description` e.g. `feat(sources): add tg_group source`
- PR must include: description of change, how tested, docs updated if needed
- PR template:
  ```markdown
  ## What
  ## Why
  ## How tested
  ## Docs updated
  ```

## Commit convention (Conventional Commits subset)
- `feat:` — new feature or source adapter
- `fix:` — bug fix
- `chore:` — maintenance (deps, config, tooling)
- `docs:` — documentation only
- `refactor:` — code change without behavior change
- Examples: `feat(sources): add telegram channel source`, `fix(dedup): handle None url gracefully`
- NO: "WIP", "update", "fix stuff", "changes", emojis in subject line

## Before submitting PR
- `uv run ruff check .` passes
- `uv run ruff format --check .` passes
- `uv run mypy .` passes
- `uv run pytest tests/` passes
- `uv run bandit -r . -ll` no high/medium issues

## Adding new dependencies
Update `docs/tech_stack.md` with rationale. Add ADR if it's an architectural choice.

## Architecture decisions
Write ADR in `docs/adr/` before implementing significant changes.

---

# Участие в разработке job_ftch

## Ветки
- `main` — готова к продакшну, защищена. Прямые пуши отключены.
- `dev` — ветка интеграции, открыта для PR от контрибьюторов.
- Ветки фич: `feat/short-description` (от dev)
- Ветки исправлений: `fix/short-description` (от dev)
- Ветки документации: `docs/short-description` (от dev)
- Ветки хотфиксов: `hotfix/short-description` (от main, редко)

## Pull Requests
- Все PR направляются в ветку `dev` (НЕ в main)
- Слияние `dev` → `main` производится только мейнтейнером репозитория после ревью
- Формат заголовка PR: `type(scope): short description` например, `feat(sources): add tg_group source`
- PR должен включать: описание изменений, способ тестирования, обновление документации при необходимости

## Соглашение о коммитах
- `feat:` — новая функциональность или адаптер источника
- `fix:` — исправление ошибки
- `chore:` — обслуживание (зависимости, конфигурация, инструменты)
- `docs:` — только документация
- `refactor:` — изменение кода без изменения поведения
- Примеры: `feat(sources): add telegram channel source`, `fix(dedup): handle None url gracefully`

## Перед отправкой PR
- `uv run ruff check .` проходит
- `uv run ruff format --check .` проходит
- `uv run mypy .` проходит
- `uv run pytest tests/` проходит
- `uv run bandit -r . -ll` без критических проблем безопасности

## Добавление новых зависимостей
Обновите `docs/tech_stack.md` с обоснованием. Добавьте ADR, если это архитектурное решение.

## Архитектурные решения
Пишите ADR в `docs/adr/` перед реализацией значимых изменений.
