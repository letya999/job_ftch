---
title: "CI/CD"
description: "Операционная карта quality gates: локальные just-команды, их соответствие GitHub Actions и порядок перед release."
updated: 2026-08-02
---
# CI/CD

`justfile` в корне даёт короткие, именованные входы для локальных quality
gates. `scripts/run_ci_checks.py` владеет повторно используемыми группами
проверок. GitHub Actions намеренно вызывает эти скрипты напрямую: workflow
остаётся читаемым, самодостаточным и не зависит от установки `just` на runner.

Перед изменением кода сначала прочитайте [architecture](../architecture.md),
а для правил работы с Markdown — [поддержку документации](../process/podderzhka_dokumentacii.md).
Перед выпуском всегда используйте [релизный чеклист](../release_checklist.md).

## Как выбрать gate

| Когда меняется | Запустить | Что подтверждает |
| --- | --- | --- |
| Любой Python-код | `just code-verify` | Ruff, форматирование, документация, config schema/layers и mypy. Это локальный эквивалент jobs `Lint & Format` и `Type Check`. |
| Границы модулей или config | `just architecture-verify` | Только import hygiene, layer boundaries и config-layer policy. |
| Обычная быстрая итерация | `just tests-smoke` | Smoke, domain/unit и Telegram adapter tests без coverage floor. |
| Изменение с широким влиянием | `just tests-all` | Полный non-network suite, test-layout guard и coverage floor 70%. |
| Документы, ADR, docs scripts или generated reference | `just setup-docs`, затем `just docs-verify` | Индексы, Markdown metadata, generated docs и строгую MkDocs-сборку. Полностью повторяет docs workflow. |
| Зависимости или security-sensitive код | `just security-verify` | Bandit, pip-audit и целостность repo-safety setup. Перед commit дополнительно выполните `ai-repo-safety scan --target .`. |
| Relevance/filtering | `just eval-filtering` | Classification gate и связанные regression tests. |
| Career-site ingest | `just eval-ingest` | Один controlled ingest batch с success-rate floor 0.65; команда использует сеть и пишет ignored artifact в `.runtime/runs/`. |
| Publication cards | `just eval-publishing` | Publication tests и card gate; при отсутствии committed fixtures fixture-eval явно пропускается. |
| Dev/prod Docker changes | `just docker-dev-verify` или `just docker-prod-verify` | Runtime image, compose config, запуск и остановку локального stack. Для env и production contract сначала прочитайте [infrastructure](infrastructure.md) и [configuration](configuration.md). |

Для узкой диагностики теста используйте `just tests-path tests/path/to_test.py`.
Команда не заменяет полный suite: она выключает project `pytest` addopts и
coverage, чтобы вывод оставался коротким.

## Состав локальных команд

`code-verify` запускает `lint` и `type` из `scripts/run_ci_checks.py`.
Группа `lint` включает `ruff check`, проверку legacy imports и границ слоёв,
docs lint, проверку трёх production YAML, config-layer policy и
`ruff format --check`; `type` запускает `mypy job_ftch`.

`docs-verify` запускает `build_index_docs.py --check`, `lint_docs.py`,
`check_docs_generated.py` и `mkdocs build --strict`. `docs-build` оставлен как
отдельная команда только для просмотра результата строгой сборки; для gate
используйте `docs-verify`.

`security-verify` объединяет прикладной security gate (`bandit`, `pip-audit`)
и проверку, что repo-safety конфигурация согласована с workflows. Сканирование
секретов, Opengrep, OSV и Scorecard остаются отдельными GitHub-hosted jobs:
они сканируют историю или используют GitHub security reporting, поэтому не
маскируются под неполный локальный эквивалент.

## GitHub Actions

Основной [CI workflow](../../.github/workflows/ci.yml) запускается на PR и
push в `main`, `dev`, `mvp-release-final`. Он состоит из независимых jobs:

- `lint`, `type-check`, `test` и `security` используют соответствующие группы
  `scripts/run_ci_checks.py`;
- `core-import-check` проверяет установку core package без optional extras;
- `release-contract` проверяет graph/runtime, classification, extraction и
  source-outcome contracts;
- `browser-smoke` устанавливает Chromium и проверяет запуск browser runtime.

`tests-smoke` полезен локально, но не заменяет CI `test`: он не измеряет
coverage и не охватывает весь suite. Для browser runtime и core-only install
нет отдельных `just` targets, поскольку они требуют специальной среды CI;
их владельцы — соответствующие jobs в `ci.yml`.

[Docs workflow](../../.github/workflows/docs.yml) повторяет `docs-verify`
на PR и push. [Docker workflow](../../.github/workflows/docker.yml) раздельно
проверяет build и запуск dev/prod runtime: он использует только
`*.env.example` и поднимает backing services, не bot. Локальные Docker gates
шире: они используют подготовленные локальные env-файлы и поднимают compose
stack целиком, поэтому их нельзя запускать без прочтения deploy-конфигурации.

Отдельные workflows охватывают то, что не следует сводить к одному локальному
скрипту: [security](../../.github/workflows/security.yml) ищет секреты,
[sast](../../.github/workflows/sast.yml) запускает Opengrep,
[supply-chain](../../.github/workflows/supply-chain.yml) запускает OSV и
dependency audit. Adapter workflows срабатывают только при изменении
соответствующего adapter path и проверяют lint/image build.

## Release

`just release-checklist` — исполняемая локальная база перед release: status,
lint/type/test/security, repository safety, core import, release contract и
docs build. Он не создаёт tag и не делает push.

После успешного ручного прохождения пунктов в [релизном чеклисте](../release_checklist.md)
можно выполнить `just release-tag VERSION`. Эта команда сначала запускает
`release-checklist`, затем создаёт annotated `vVERSION` tag и пушит только tag;
она требует явного подтверждённого права на публикацию. Push тега запускает
[release workflow](../../.github/workflows/release.yml), который создаёт
GitHub Release с generated notes.
