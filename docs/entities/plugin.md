---
title: "Plugin (Плагин и реестр плагинов)"
description: "Plugin (плагин) — это механизм регистрации и discovery"
updated: 2026-07-24
---
# Plugin (Плагин и реестр плагинов)

## Что это такое

Plugin (плагин) — это механизм регистрации и discovery
реализаций. Это не отдельная архитектурная роль.

Объект может быть port adapter, monitor, scraper, parser или
assessment adapter по ответственности и одновременно
подключаться plugin-style через registry/entry points.
Сама система (ядро) не знает ни о каких конкретных
реализациях (например, она не знает, что такое Telegram API
или PostgreSQL).
Ядро работает через абстракции, а registry подключает
конкретные реализации во время запуска.

## Зачем это нужно и ПОЧЕМУ так устроено

Архитектура плагинов решает проблему "раздувания" ядра (core
bloat) и зависимости от сторонних библиотек.
Если бы каждый источник (Telegram, LinkedIn, Greenhouse) был
жестко закодирован в ядре, проекту пришлось бы устанавливать
сотни библиотек (`telethon`, `playwright`, `httpx` и т.д.).

Благодаря реестру, плагины могут лежать в отдельных
python-пакетах (third-party plugins).
Вы устанавливаете ядро, а потом устанавливаете только те
плагины, которые вам нужны, избегая лишних зависимостей.

Важно четко различать Plugin и adapter taxonomy:
- **Plugin** — способ подключения реализации.

- **Port adapter** — реализация одного application port, например `Source`,
  `Store`, `Sink`, `LLMProvider`.

- **Runtime adapter** — внешний вход в систему: Telegram bot, MCP, FastAPI,
  FastStream, Dagster.

- **Assessment adapter** — pre-ingest оценка `SourceSpec` и назначение ingest
  strategy.

## Как это работает изнутри

Все доступные категории плагинов перечислены в `PluginKind`
(enum):

```python SOURCE         # Поставщик данных (Source Protocol)
SINK  # Получатель данных (Sink Protocol)
NODE  # Узел обработки (Stage Protocol)
STORE  # Хранилище pipeline (Store Protocol)
JOB_GROUP_STORE  # Хранилище групп вакансий BYPASS         # Обход антибот-защит (BypassStrategy Protocol)
LLM  # Языковая модель (LLMProvider Protocol)
EMBEDDING  # Векторизация текста (EmbeddingProvider Protocol)
VECTOR  # Векторное хранилище (VectorBackend Protocol)
JOB_BACKEND  # Хранилище вакансий (JobPersistenceBackend Protocol)
SEARCH_BACKEND  # Полнотекстовый поиск (SearchBackend Protocol)
AUTH  # Провайдер credentials (AuthProvider Protocol)
MONITOR  # Монитор карьер-сайта SCRAPER        # HTML скрапер PARSER         # Парсер структуры страницы RERANKER       # Reranking для поиска
SOURCE_ASSESSMENT  # Pre-ingest source assessment adapter
```

### Жизненный цикл плагина (PluginState)

Каждый плагин в реестре проходит через состояния:
- `PENDING`: Плагин зарегистрирован (был вызван декоратор `@register`), но его фабрика ещё ни разу не вызывалась.

- `ACTIVE`: Фабрика плагина успешно вызвалась, он загружен и работает.

- `DISABLED`: При загрузке возникла `ImportError`. Внимание: **это нормально!** Если пользователь не установил опциональные зависимости (`pip install job-ftch[openai]`), плагин для OpenAI просто станет `DISABLED`, а не уронит всё приложение.

- `ERROR`: Фабрика вызвала неожиданную ошибку (например, неверный конфиг).

## Как написать и зарегистрировать свой плагин

Есть два способа добавить плагин в систему:

### Способ 1 — Декораторы (для встроенных плагинов)

Если вы пишете код прямо внутри репозитория `job_ftch`,
используйте декораторы:

```python from job_ftch.application.registry import register_source
# Указываем имя, версию и опциональные библиотеки, которые требуются
@register_source("my_custom_api", version="1.0.0", requires_extras=("my_extra",))
def create_my_source(settings):
    # Эта фабрика будет вызвана реестром, когда потребуется этот источник
    return MyAPISource(settings)
```

### Способ 2 — Entry Points (для сторонних пакетов)

Если вы делаете свой независимый пакет `my_company_plugin` и
хотите, чтобы `job_ftch` нашел его:

1. В `pyproject.toml` вашего пакета:

```toml
[project.entry-points."job_ftch.sources"]
my_api = "my_pkg.sources:register"
```

2. В коде (`my_pkg/sources.py`):

```python def register():
from job_ftch.application.registry import register_source
from .my_source import MyAPISource


@register_source("my_api")
def factory(settings):
    return MyAPISource(settings)
```

При старте приложения вызов функции `load_extensions()`
просканирует все `entry_points` и загрузит внешние плагины.

## Интроспекция: как узнать, что загружено

Вы можете динамически просматривать список загруженных
плагинов через `PluginRegistry`:

```python from job_ftch.application.plugin_registry import _default_registry from job_ftch.application.plugin import PluginKind from job_ftch.application.registry import load_extensions
load_extensions()

# Узнаем все зарегистрированные источники sources = _default_registry.list_by_kind(PluginKind.SOURCE)
for entry in sources:
    print(f"{entry.descriptor.name}: {entry.state}")

# Вывод:
# telegram_channel: active
# career_site: active
# my_api: disabled (если нет зависимостей)
```

## Типичные ошибки и что нельзя делать

1. **Регистрация без фабрики.**

Декораторы `@register_*` должны вешаться не на класс
реализации, а на *функцию-фабрику*, которая возвращает
инстанс этого класса.
Фабрике передаются настройки.

2. **Паника при `DISABLED` статусе.**

Если вы видите в логах, что плагин `DISABLED` — это фича, а
не баг.
Система gracefully отключает плагины, если для них нет
библиотек.
Если вам нужен этот плагин, просто установите его
зависимости.

3. **Импорт тяжелых зависимостей вне фабрики.**

В файле плагина импортируйте тяжелые библиотеки (вроде
`torch` или `playwright`) *внутри* фабрики или функции, а не
на уровне модуля.
Иначе ядро будет грузить эти мегабайты даже тогда, когда
плагин не используется.

## Связи с другими сущностями

- [Adapters and plugins](adapters_and_plugins.md) — canonical taxonomy для
  port adapters, runtime adapters, assessment adapters и plugin mechanism.

- [Runtime Adapters](runtime_adapters.md) — runtime adapters используют ядро и
  plugin-connected implementations, но сами не являются `PluginKind`.
