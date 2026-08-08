---
title: "Как поддерживать документацию"
description: "Как обновлять Markdown-документацию, ADR и generated docs в этом репозитории."
updated: 2026-08-02
---
# Как поддерживать документацию

Этот документ описывает, как в репозитории обновлять обычные Markdown-доки,
ADR и generated docs так, чтобы навигация и проверки оставались согласованными.

## Что обязательно у каждого Markdown-файла

Все поддерживаемые Markdown-документы в `docs/`, `scripts/`, `tests/` и
`fixtures/` должны начинаться с YAML front matter.

Обязательные поля:

- `title` — человекочитаемое название.
- `description` — короткое описание содержимого.
- `updated` — дата последнего осмысленного обновления в формате `YYYY-MM-DD`.

Пример:

```yaml
---
title: "Authentication Provider"
description: "Architecture and design of the auth provider module."
updated: 2026-07-27
---
```

## Базовый порядок работы

1. Измените исходный документ.
2. Обновите `updated`.
3. Если изменился смысл документа, обновите `title` и `description`.
4. Если документ переехал, переименовался или добавился новый раздел, пересоберите индексы.
5. Если документ generated, не правьте его руками: обновите источник генерации и затем перегенерируйте файл.

## ADR

ADR лежат в `docs/adr/` и подчиняются тем же правилам front matter.

Дополнительно:

- ADR должен отражать текущее состояние решения, а не только исторический замысел.
- Если реализация ушла дальше, сначала обновите статус ADR.
- Если старое решение заменено, создайте новый ADR, который его supersede-ит, а не оставляйте старый как будто он всё ещё актуален.

## Какие скрипты использовать

### Индексы

```bash
uv run python scripts/build_index_docs.py
```

Скрипт пересобирает `index.md` по деревьям `docs/`, `scripts/`, `tests/` и
`fixtures/`. Его нужно запускать после добавления, удаления, переезда или
переименования документов.

### Линтер документации

```bash
uv run python scripts/lint_docs.py
```

Проверяет:

- наличие `title`, `description`, `updated`;
- валидность даты;
- актуальность generated `index.md`.

### Проверка generated docs

```bash
uv run python scripts/check_docs_generated.py
```

Сейчас она валидирует generated-файлы:

- `docs/nodes/reference.md`
- `docs/pipelines/graphs.md`

Если они устарели, нужно перегенерировать их через
`uv run python scripts/build_graph_reference.py`.

### Поиск устаревших документов

```bash
uv run python scripts/stale_docs.py --days 90
```

Помогает найти документы, которые давно не пересматривались.

## Что нельзя править вручную

- generated `index.md`;
- generated reference docs с маркером `GENERATED`.

Для них меняют источник, затем запускают генератор.

## Рекомендуемый локальный цикл

```bash
just setup-docs
just docs-verify
```

`docs-verify` — полный gate: актуальность индексов, Markdown metadata,
generated reference docs и `mkdocs build --strict`. Он повторяет
`.github/workflows/docs.yml`. Для одной строгой сборки без остальных проверок
используйте `just docs-build`.

Если добавился, переехал или удалён Markdown-файл, сначала пересоберите
индексы, затем проверьте их:

```bash
uv run python scripts/build_index_docs.py
just docs-verify
```

Для выбора code, architecture, test и release gates смотрите
[operations/ci-cd](../operations/ci-cd.md). Если документация меняется вместе
с runtime/config, прочитайте [operations/configuration](../operations/configuration.md)
и включите соответствующий `just` gate из CI/CD карты.

## Что уже проверяет CI

- `.github/workflows/docs.yml` выполняет тот же полный набор, что и
  `just docs-verify`.
- `uv run python scripts/run_ci_checks.py lint` также включает
  `scripts/lint_docs.py`, но не заменяет docs gate: в нём нет
  `check_docs_generated.py` и строгой MkDocs-сборки.
