---
title: "Система плагинов (Plugins)"
description: "**Слой**: application"
updated: 2026-07-24
---
# Система плагинов (Plugins)

**Слой**: application
**Файл**: `job_ftch/application/plugin.py`, `job_ftch/application/plugin_registry.py`

## Что это

Система плагинов позволяет расширять функциональность ядра `job_ftch` без
изменения его исходного кода.
Плагин — это зарегистрированная реализация или extension hook. Это способ
подключения, а не отдельная архитектурная роль: port adapters, monitors,
scrapers, parsers и assessment adapters могут подключаться plugin-style.

## Основные сущности

### PluginKind (Enum)
Определяет роль плагина в системе:
*   `SOURCE` — поставщик данных (`Source` Protocol).
*   `SINK` — вывод данных (`Sink` Protocol).
*   `NODE` — узел обработки (`Stage` Protocol).
*   `STORE` — хранилище состояний (`StoreConnector` Protocol).
*   `JOB_GROUP_STORE` — хранилище групп вакансий.
*   `LLM` — провайдер языковых моделей.
*   `EMBEDDING` — провайдер векторных представлений.
*   `VECTOR` — бэкенд векторного поиска.
*   `JOB_BACKEND` — бэкенд хранения вакансий.
*   `SEARCH_BACKEND` — бэкенд полнотекстового поиска.
*   `AUTH` — провайдер учётных данных.
*   `BYPASS` — стратегия обхода защит.
*   `MONITOR` — монитор карьер-сайта.
*   `SCRAPER` — скрапер страниц вакансий.
*   `PARSER` — парсер HTML-структуры.
*   `SOURCE_ASSESSMENT` — pre-ingest source assessment adapter.

### PluginState (Enum)
Состояния жизненного цикла плагина:
1.  `PENDING` — обнаружен, но не инициализирован.
2.  `ACTIVE` — успешно загружен и готов к работе.
3.  `DISABLED` — отключен пользователем или системой.
4.  `ERROR` — ошибка при инициализации или работе.

### PluginDescriptor
Метаданные плагина: имя, версия, автор, зависимости и ссылка на фабрику
(callable), которая создает экземпляр плагина.

## PluginRegistry
Центральный реестр всех доступных расширений. `PipelineBuilder` запрашивает
плагины из этого реестра по их имени.

## Когда создаётся / откуда берётся

Плагины обнаруживаются автоматически через Python entry points или регистрируются
явно с помощью декораторов `@register_source`, `@register_node` и т.д.

## Куда идёт после

Экземпляры плагинов создаются в соответствующем composition path. Sources,
sinks и nodes обычно создаются при сборке pipeline; assessment adapters
используются раньше, во время runtime source onboarding.

## Пример (Custom Source Plugin)

```python
# Способ 1: декоратор (встроенные и пакетные плагины)
from job_ftch.application.registry import register_source


@register_source("my_custom_api")
def create_my_source(settings):
    return MyCustomSource(settings)


# Способ 2: entry point в pyproject.toml (сторонние пакеты)
# [project.entry-points."job_ftch.sources"]
# my_api = "my_pkg.sources:register"
```

Декораторы по видам плагина:
`@register_source`, `@register_sink`, `@register_store`, `@register_bypass`,
`@register_llm`, `@register_embedding_provider`, `@register_vector_backend`,
`@register_job_backend`, `@register_search_backend`, `@register_auth_provider`,
`register_source_assessment_adapter`.

## Связанные сущности

- [Adapters and plugins](adapters_and_plugins.md)
- [Source Assessment Adapter](source_assessment_adapter.md)

*   `PipelineBuilder` — использует реестр для сборки пайплайна.
*   `RuntimeAdapter` — может добавлять свои плагины в реестр при старте.
