# RuntimeAdapter (Рантайм Адаптеры)

## Что это такое

RuntimeAdapter — это внешний слой архитектуры, оболочка
("обёртка"), которая берёт полностью настроенный пайплайн
(`PipelineBuilder`) и подключает его к конкретному внешнему
окружению (веб-серверу, очереди сообщений, оркестратору
задач).

В отличие от [плагинов](plugin.md), адаптеры **знают** о
внешних фреймворках (импортируют FastAPI, Dagster, aiogram)
и **не регистрируются** в внутреннем реестре плагинов.
Их задача — переводить запросы из внешнего мира (HTTP,
Kafka, Cron) в вызовы `pipeline.run()`.

## Зачем это нужно и ПОЧЕМУ так устроено

Ядро парсера (`job_ftch/application`) написано как чистая
Python-библиотека.
Оно ничего не знает о REST API, о Telegram-ботах или о
расписаниях Dagster. 

Дизайн-решение вынести все фреймворки в слой Runtime
Adapters (`job_ftch/adapters/`) делает ядро абсолютно
независимым и переиспользуемым.
Вы можете запустить один и тот же код:
- Как CLI-скрипт (без адаптеров вообще).

- Как REST API (FastAPI адаптер).

- Как фоновую задачу (Dagster адаптер).

- Как MCP-сервер для AI-агентов.

- Как Telegram-бота.

## Главное правило изоляции (builder.clone)

Самый критичный архитектурный момент любого адаптера —
изоляция состояния запросов.
Если 10 пользователей одновременно обратятся к
FastAPI-серверу с просьбой запустить разные источники, они
не должны влиять друг на друга.

Адаптер создаётся снаружи, получает на вход
`PipelineBuilder`, но *создаёт* объект `Pipeline` только в
момент получения конкретного запроса.
Для этого обязательно используется метод `builder.clone()`.

**ПРАВИЛЬНО:**
```python
# Каждый HTTP запрос получает свежую копию пайплайна async def run_pipeline(source_spec):
    # clone() копирует конфигурацию (узлы, store, sinks), но не сами источники!
    pipeline = builder.clone().sources([source_spec]).build()
    summary = await pipeline.run_async()
    return summary.as_dict()
```

**ОШИБКА:**
Если не использовать `clone()`, `builder` будет накапливать
в себе `source_spec` от всех одновременных запросов, и вы
начнете парсить чужие источники, перетирая конфигурации.

## Встроенные адаптеры

### 1. FastAPI Adapter Предоставляет стандартный REST API.
```python from adapters.fastapi.adapter import create_app

app = create_app(builder, search_backend=search_backend)
# Эндпоинты:
# POST /pipeline/run — запустить пайплайн для конкретного SourceSpec.
# GET  /pipeline/status — статус последних запусков.
# GET  /jobs/search?q=... — поиск по базе вакансий.
```
Применение: Встраивание парсера как микросервиса в
существующий бэкенд.

### 2. MCP Adapter (TenantMCPServer)
Предоставляет Model Context Protocol (MCP) сервер для
интеграции с AI-агентами (например, Claude Desktop).
```python from adapters.mcp.server import create_server

server = create_server(configs_dir="config/tenants")
await server.startup()
server.run(transport="stdio")
```
Реализует более 17 инструментов: запуск, управление
профилями, просмотр логов.
Позволяет языковой модели (LLM) самостоятельно управлять
пайплайнами.

### 3. Dagster Adapter Интеграция с ETL-оркестратором Dagster.
```python from adapters.dagster.adapter import create_definitions

# Превращает конфигурации (SourceSpec) в Asset'ы Dagster defs = create_definitions(builder, specs=source_specs)
```
Применение: Запуск скрапинга по жесткому расписанию (cron),
визуализация графа зависимостей, мониторинг падений в
enterprise-среде.

### 4. FastStream Adapter Интеграция с брокерами сообщений (Kafka, RabbitMQ, NATS).
```python from adapters.faststream.adapter import register_faststream_handlers

register_faststream_handlers(
    broker,
    subject="jobs.trigger",
    publish_subject="jobs.results",
    builder=builder,
)
```
Применение: Событийно-ориентированная архитектура.
Сервис "просыпается", когда в топик Kafka падает сообщение с
`SourceSpec`, парсит, и кладет результат в другой топик.

### 5. Telegram Bot Полноценный бот на `aiogram` (находится в `job_ftch/adapters/telegram_bot/`).
Позволяет администраторам или пользователям напрямую через
чат добавлять каналы, запускать парсинг и смотреть
результаты (RunSummary).

## Типичные ошибки и что нельзя делать

1. **Забывать builder.clone() в обработчиках запросов.**

Это приведет к утечкам состояния и критическим багам в
конкурентной среде (race conditions).
Всегда клонируйте билдер перед вызовом `.build()`.

2. **Загружать тяжелую логику в адаптер.**

Адаптер должен быть "тупым".
Его задача — принять JSON/сообщение, десериализовать в
доменный объект (например, `SourceSpec`), вызвать пайплайн и
вернуть результат.
Вся логика валидации текста или дедупликации должна
оставаться внутри ядра.

3. **Смешивать адаптер и плагин.**

Никогда не используйте `@register` для адаптеров.

## Связи с другими сущностями

- [PipelineBuilder](pipeline_builder.md) — главный объект, который адаптер инкапсулирует в себе.

- [Plugin](plugin.md) — адаптеры не являются плагинами, так как зависят от конкретных внешних инфраструктурных библиотек.

- [SourceSpec](source_spec.md) — часто приходит в адаптер "снаружи" (через HTTP запрос или сообщение).
