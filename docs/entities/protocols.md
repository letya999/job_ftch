---
title: "Базовые протоколы"
description: "**Слой**: `application`"
updated: 2026-07-24
---
# Базовые протоколы

**Слой**: `application`
**Файл**: `job_ftch/application/contracts.py`

В `job_ftch` большинство интеграционных точек оформлены через
`typing.Protocol + @runtime_checkable`.

## 1. Source[T]

```python
def fetch(self) -> AsyncIterator[T | QuarantinedRawItem]
```

Source поставляет валидные входящие элементы или quarantine payloads.

## 2. Stage[In, Out]

```python
async def process(self, item: In) -> Out | None
```

Stage может:

- пропустить item дальше
- трансформировать item в другой тип
- вернуть `None` и тем самым дропнуть item

Это единственный нормальный механизм type-changing внутри core.

## 3. Sink[T]

```python
async def emit(self, item: T) -> None
```

Sink получает финальный item и пишет его наружу.

Дополнительный протокол `FlushableSink` поддерживает:

```python
async def flush(self) -> None
```

## 4. Store

`Store` отвечает не за job catalog, а за operational state пайплайна:

- processed items
- dedup keys
- duplicate records
- arbitrary run state
- cached source strategies
- per-source snapshots

Именно через этот порт работают `DedupNode`, snapshot filtering и run state.

## 5. AuthProvider

```python
def resolve(self, source_id: str) -> dict[str, str]
```

Разделяет `SourceSpec` и секреты.

## 6. LLMProvider

```python
async def extract(self, text: str, schema: type[T]) -> T
async def classify(self, prompt: str, schema: type[Any]) -> Any
async def present(self, job_payload: str, schema: type[Any]) -> Any
async def generate_text(...)
```

Один порт покрывает три текущие LLM-точки:

- extraction
- borderline relevance classification
- presentable text generation

## Другие порты

В том же модуле описаны:

- `JobPersistenceBackend`
- `SearchBackend`
- `EmbeddingProvider`
- `VectorBackend`
- `IngestMode`
- `BypassStrategy`
- `TranslatorPort`
- `LanguageDetectorPort`

## Практическое правило

Если вы добавляете новую возможность, сначала проверьте, не укладывается ли она
в существующий port. Новый протокол имеет смысл только тогда, когда нужен новый
устойчивый boundary, а не просто ещё один helper.
