# Архитектура job_ftch

## Обзор
Проект использует **Гексагональную архитектуру** (Ports & Adapters). Основная бизнес-логика (Domain) находится в центре, а внешние зависимости и детали реализации (Инфраструктура) — снаружи.

## 5 протоколов (порты)
1. **Source** — асинхронный итератор `RawItem`; при pre-validation ошибках источник может отдать `QuarantinedRawItem` в quarantine flow.
2. **Node** — `async process(item) -> T | None`, при этом `SanitizeNode` всегда первый (очистка, нормализация, AI-фильтрация, дедупликация).
3. **Sink** — `async emit(item: T)`; в production обычно `Job`, в debug-режиме допустим `RawItem`.
4. **Store** — интерфейс для хранения состояния (был ли пост обработан, сохранение вакансии).
5. **LLMProvider** — `extract[T](text, schema) -> T` (структурированное извлечение данных через LLM).

## Слои системы
- `domain/` — чистые Pydantic-модели (Job, RawItem, QuarantinedRawItem). Никаких побочных эффектов или I/O.
- `application/` — логика пайплайна (`pipeline.py`), контракты и сценарии использования.
- `infrastructure/` — реализация адаптеров (Telegram Client, HTTP, БД, LLM SDK).
- `nodes/` — конкретные шаги обработки (SanitizeNode, DedupNode, AIGateNode).
- `sinks/` — выходные адаптеры (JsonFileSink, TelegramPublishSink).
- `app.py` — точка сборки (Composition Root). Собирает пайплайн из конфигурации.

## Потоки данных
```
Source.fetch() → [Node...] → Sink.emit()
      ↘
       quarantine → QuarantineSink.emit()
                      ↕
                    Store
```

## Подход к разработке
- **Light DDD**: Использование понятий Entity, Value Object и Repository без излишней сложности.
- **TDD**: Тестирование узлов (Nodes) изолированно с использованием InMemoryStore. Чистые функции домена тестируются без моков.
- **Телеметрия**: Использование OpenTelemetry API/SDK с первого дня для обеспечения наблюдаемости (observability) без привязки к конкретному вендору.

## Безопасность
- `SanitizeNode` всегда является первым в цепочке обработки.
- Извлечение данных только в структурированном виде через `instructor`.
- Список разрешенных URL для карьерных сайтов.
