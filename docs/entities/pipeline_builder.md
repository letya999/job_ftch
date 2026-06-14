# PipelineBuilder (Строитель Пайплайнов)

## Что это такое

`PipelineBuilder` — это главный фасад ядра (fluent API),
через который разработчики конструируют пайплайн обработки.
Вместо того чтобы вручную создавать массивы узлов,
передавать зависимости и собирать всё воедино, вы вызываете
цепочку методов `.stage()`, `.source()`, `.sink()`,
описывая, что пайплайн должен делать. 

В конце цепочки вызывается метод `.build()`, который
валидирует всю конфигурацию и возвращает готовый к запуску
объект `Pipeline`.

## Зачем это нужно и ПОЧЕМУ так устроено

Пайплайн в `job_ftch` — это сложный граф объектов
(источники, узлы обработки, получатели, хранилище).
Узлы должны идти в определенном порядке (Sanitize всегда
первый).
Нельзя запустить пайплайн без получателя (Sink), иначе
данные уйдут в никуда.
Нельзя делать дедупликацию без Store.

Дизайн-решение (паттерн Builder) решает три задачи:
1. **Валидация до старта**: Метод `.build()` не даст вам создать сломанный пайплайн. Он выбросит `ValueError`, если вы забыли `SanitizeNode` или не добавили ни одного `Source`. Это защита от глупых ошибок в рантайме.

2. **Читаемость кода**: Цепочка вызовов (fluent interface) читается как текст и сразу дает понимание, как движутся данные.

3. **Клонирование (Изоляция)**: Позволяет создать один "шаблонный" конфигуратор при старте сервера, а затем легко копировать его для каждого входящего запроса.

## Основной паттерн использования

Пример создания пайплайна в коде (из
`application/builder.py`):

```python from job_ftch import PipelineBuilder from job_ftch.domain.source_spec import TelegramChannelSpec from job_ftch.nodes import SanitizeNode, DedupNode, HardFilterNode from job_ftch.nodes.extraction import ExtractionNode from job_ftch.sinks.json_file import JsonFileSink from job_ftch.infrastructure.stores.sqlite import SQLiteStore from job_ftch.infrastructure.llm.openai_provider import OpenAIProvider

# 1. Строим пайплайн pipeline = (
    PipelineBuilder()
    .source(TelegramChannelSpec(type="telegram_channel", entity="ai_jobs"))
    .store(SQLiteStore(".runtime/store.db"))
    
    # Узлы идут строго в порядке добавления
    .stage(SanitizeNode())              # ВСЕГДА первый
    .stage(HardFilterNode(profile))     # Дешевые фильтры
    .stage(DedupNode())                 # Отсечение дублей (использует Store)
    .stage(ExtractionNode(llm=OpenAIProvider())) # Дорогая LLM-операция
    
    .sink(JsonFileSink("artifacts/jobs.jsonl"))
    .build() # <- Здесь происходит валидация и создание Pipeline
)

# 2. Запускаем summary = await pipeline.run(max_items=500)
```

## Механика клонирования: зачем нужен clone()

В веб-фреймворках (например, FastAPI адаптере) один инстанс
сервера обрабатывает множество запросов одновременно.
Если бы они использовали один и тот же `PipelineBuilder`,
они бы перезаписывали конфигурацию друг друга.

Метод `builder.clone()` создает глубокую копию билдера со
всеми настройками (узлы, Store, Sink-и), **кроме
источников**.
Это гарантирует, что каждый HTTP-запрос получает
изолированный пайплайн без разделяемого состояния (shared
state):

```python
# Базовая конфигурация при старте приложения base_builder = PipelineBuilder().store(db).stage(SanitizeNode()).sink(webhook)

# Обработчик каждого отдельного запроса async def handle_request(source_spec):
    # Клонируем, добавляем источник ТОЛЬКО для этого запроса, билдим
    pipeline = base_builder.clone().sources([source_spec]).build()
    return await pipeline.run()
```

Без `clone()` один запрос мог бы случайно спарсить источники
из соседнего запроса.

## Альтернатива: configure() из YAML

Если вы не хотите писать Python-код, `PipelineBuilder` можно
инициализировать из декларативного YAML-конфига с помощью
обертки `configure()`:

```python from job_ftch import configure

# Автоматически загрузит SourceSpec-ы, профили, подключит Store и Sink из файла builder = configure("config/tenant.yaml")

pipeline = builder.build()
await pipeline.run()
```

Это особенно полезно для CLI и ETL-интеграций.

## Разница между run() и run_async()

Готовый пайплайн можно запустить двумя методами:
- `run_async()` — чистая корутина. Используйте её, если вы уже находитесь в `async def` функции (например, внутри FastAPI или aiogram).

- `run()` — синхронная обертка, которая под капотом вызывает `asyncio.run()`. Используйте её в обычных скриптах верхнего уровня, где цикл событий еще не запущен.

Оба метода возвращают объект `RunSummary` (статистику
выполнения).

## Типичные ошибки и что нельзя делать

1. **Забыть добавить SanitizeNode.**

`.build()` упадет с ошибкой "Pipeline must start with
SanitizingNode".
Это сделано намеренно для безопасности (защита от инъекций и
PII утечек).

2. **Забыть добавить Store.**

Если вы добавили в цепочку `DedupNode`, но не вызвали
`.store()`, пайплайн упадет при сборке.
Дедупликация и хранение курсоров не могут работать без
Store.

3. **Игнорировать clone() в многопоточной/асинхронной среде.**

Использование одного и того же билдера в нескольких
конкурентных тасках приведет к непредсказуемым гонкам данных
(race conditions).
Всегда используйте `clone()`.

## Связи с другими сущностями

- [Stage](stage_node.md), [Source](source.md), [Sink](sink.md), [Store](store.md) — компоненты, из которых строится пайплайн.

- [RuntimeAdapters](runtime_adapters.md) — адаптеры активно используют `PipelineBuilder` для интеграции ядра во внешний код.

- [RunSummary](run_summary.md) — то, что возвращается после вызова `.run()`.
