# Архитектура job_ftch

## Обзор
Проект использует **Гексагональную архитектуру** (Ports & Adapters). Основная бизнес-логика (Domain) находится в центре, а внешние зависимости и детали реализации (Инфраструктура) — снаружи.

## 5 протоколов (порты)
1. **Source** — асинхронный итератор `RawItem`; при pre-validation ошибках источник может отдать `QuarantinedRawItem` в quarantine flow.
2. **Stage / Node** — `async process(item) -> T | None`. Type transitions happen through `Stage[In, Out]`; same-type runtime nodes use `SanitizingNode` and `ProcessingNode`.
3. **Sink** — `async emit(item: T)`; buffered sinks implement `FlushableSink.flush()`. В production обычно принимает `Job`/envelope, в debug-режиме допустим `RawItem`.
4. **Store** — интерфейс для хранения состояния: processed IDs, dedup keys, duplicate records, source cursors, and run state.
5. **LLMProvider** — `extract[T](text, schema) -> T` (структурированное извлечение данных через LLM).

## Слои системы
- `domain/` — чистые Pydantic-модели (Job, RawItem, QuarantinedRawItem). Никаких побочных эффектов или I/O.
- `application/` — логика пайплайна (`pipeline.py`), контракты и сценарии использования.
- `infrastructure/` — реализация адаптеров (Telegram Client, HTTP, PostgreSQL, LLM SDK).
- `nodes/` — конкретные шаги обработки (`SanitizeNode`, `DedupNode`, `TriageNode`, будущие Extract/Score nodes).
- `sinks/` — выходные адаптеры (JsonFileSink, TelegramPublishSink).
- `app.py` — точка сборки (Composition Root). Собирает пайплайн из конфигурации.

## Потоки данных
```
Source.fetch() → SanitizingNode → [ProcessingNode...] → Sink.emit() → FlushableSink.flush()
      ↘
       quarantine → QuarantineSink.emit()
                      ↕
                    Store
```

`SanitizeNode` всегда первый. Текущая local/debug chain:

```text
SanitizeNode -> optional ProcessingNode stages -> Sink.emit()
```

Pipeline records source, sanitize, drop, emit, quarantine, failure, extraction,
duplicate, per-source, and per-stage counters in `RunSummary`. Source-level
`QuarantinedRawItem` records bypass processing nodes and go directly to
quarantine output.

## Подход к разработке
- **Light DDD**: Использование понятий Entity, Value Object и Repository без излишней сложности.
- **TDD**: Тестирование узлов (Nodes) изолированно с использованием `InMemoryStore`; persistent idempotency uses `PostgresStore`. Чистые функции домена тестируются без моков.
- **Телеметрия**: Использование OpenTelemetry API/SDK с первого дня для обеспечения наблюдаемости (observability) без привязки к конкретному вендору.

## Безопасность
- `SanitizeNode` всегда является первым в цепочке обработки.
- Извлечение данных только в структурированном виде через `instructor`.
- Список разрешенных URL для карьерных сайтов.
