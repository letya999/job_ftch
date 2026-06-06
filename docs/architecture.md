# Архитектура job_ftch

## Обзор
Проект использует **Гексагональную архитектуру** (Ports & Adapters). Основная бизнес-логика (Domain) находится в центре, а внешние зависимости и детали реализации (Инфраструктура) — снаружи.

## 5 протоколов (порты)
1. **Source** — асинхронный итератор `RawItem`; при pre-validation ошибках источник может отдать `QuarantinedRawItem` в quarantine flow.
2. **Node** — `async process(item, context) -> NodeOutcome[T]`. Outcome явно описывает `pass`, `drop`, `quarantine` или `fail`; у каждого node есть `name`, `stage` и `is_sanitize`.
3. **Sink** — `async emit(item: T)` и `async finalize()`. В production обычно принимает `Job`/envelope, в debug-режиме допустим `RawItem`.
4. **Store** — интерфейс для хранения состояния (был ли пост обработан, сохранение вакансии).
5. **LLMProvider** — `extract[T](text, schema) -> T` (структурированное извлечение данных через LLM).

## Слои системы
- `domain/` — чистые Pydantic-модели (Job, RawItem, QuarantinedRawItem). Никаких побочных эффектов или I/O.
- `application/` — логика пайплайна (`pipeline.py`), контракты и сценарии использования.
- `infrastructure/` — реализация адаптеров (Telegram Client, HTTP, PostgreSQL, LLM SDK).
- `nodes/` — конкретные шаги обработки (`SanitizeNode`, `ValidateRawNode`, `OriginPolicyNode`, будущие Dedup/Extract/Score nodes).
- `sinks/` — выходные адаптеры (JsonFileSink, TelegramPublishSink).
- `app.py` — точка сборки (Composition Root). Собирает пайплайн из конфигурации.

## Потоки данных
```
Source.fetch() → [NodeOutcome-aware Node...] → Sink.emit() → Sink.finalize()
      ↘
       quarantine → QuarantineSink.emit()
                      ↕
                    Store
```

`SanitizeNode` всегда первый. Текущая raw-protection цепочка:

```text
SanitizeNode -> ValidateRawNode -> OriginPolicyNode -> Sink.emit()
```

После всех node pipeline проверяет idempotency state и только потом пишет в
основной sink. При drop/quarantine/fail pipeline записывает стадию и причину в
`RunSummary`; при наличии quarantine sink событие остаётся инспектируемым как
structured output.

## Подход к разработке
- **Light DDD**: Использование понятий Entity, Value Object и Repository без излишней сложности.
- **TDD**: Тестирование узлов (Nodes) изолированно с использованием InMemoryStore. Чистые функции домена тестируются без моков.
- **Телеметрия**: Использование OpenTelemetry API/SDK с первого дня для обеспечения наблюдаемости (observability) без привязки к конкретному вендору.

## Безопасность
- `SanitizeNode` всегда является первым в цепочке обработки.
- `OriginPolicyNode` отвечает за Telegram host policy, career-site allowlist и
  rejection local/private career-site hosts.
- Извлечение данных только в структурированном виде через `instructor`.
- Список разрешенных URL для карьерных сайтов.
