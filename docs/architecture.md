# Архитектура job_ftch

## Обзор
Проект использует **Гексагональную архитектуру** (Ports & Adapters). Основная бизнес-логика (Domain) находится в центре, а внешние зависимости и детали реализации (Инфраструктура) — снаружи.

## 5 протоколов (порты)
1. **Source** — асинхронный итератор `RawItem`; при pre-validation ошибках источник может отдать `QuarantinedRawItem` в quarantine flow.
2. **Node / Stage** — `async process(item) -> T | None`; same-type этапы остаются `ProcessingNode[T]`, а смена типа делается только через `Stage[In, Out]` boundary. `SanitizeNode` всегда первый.
3. **Sink** — `async emit(item: T)`; основной production output — `Job`, а quarantine / rejected / review / posting идут отдельными sink'ами.
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
Source.fetch()
  → SanitizeNode
  → HeuristicTriageNode
  → DedupNode
  → ExtractionNode (RawItem -> Job)
  → [validation / normalization / relevance / scoring]
  → main Job sink
         ↘ review sink
         ↘ posting sink

quarantine / rejected side-channels
         ↕
       Store
```

## Реальный pipeline shape
- `RawItem` живёт только до extraction boundary.
- `ExtractionNode` строит `Job` в статусе `complete` или `partial`.
- После extraction работают только `Stage[Job, Job]`: validation, title/company normalization, location/work-mode normalization, compensation parsing, AI-role relevance filtering, quality scoring.
- Borderline jobs не теряются: они попадают в основной output и параллельно в review sink.
- Дропы, quarantine и source failures дополнительно пишутся в rejected sink для operator feedback loop.

## Подход к разработке
- **Light DDD**: Использование понятий Entity, Value Object и Repository без излишней сложности.
- **TDD**: Тестирование узлов (Nodes) изолированно с использованием InMemoryStore. Чистые функции домена тестируются без моков.
- **Телеметрия**: Использование OpenTelemetry API/SDK с первого дня для обеспечения наблюдаемости (observability) без привязки к конкретному вендору.

## Безопасность
- `SanitizeNode` всегда является первым в цепочке обработки.
- Production extraction идёт в структурированном виде через `instructor`; локальный heuristic backend допустим только как offline/dev fallback.
- Список разрешенных URL для карьерных сайтов.
- Outbound posting должен проходить через отдельный sink и уважать `dry-run`.
