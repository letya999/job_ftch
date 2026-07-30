---
title: "LLMProvider"
description: "**Слой**: `application`"
updated: 2026-07-24
---
# LLMProvider

**Слой**: `application`
**Файл**: `job_ftch/application/contracts.py`

## Что это

`LLMProvider` — единый порт для текущих LLM-вызовов в системе.

Это уже не только extraction interface. Сейчас через него проходят три разные
категории задач:

- structured extraction
- borderline relevance classification
- presentable text generation

## Текущий контракт

```python
async def extract(self, text: str, schema: type[T]) -> T
async def classify(self, prompt: str, schema: type[Any]) -> Any
async def present(self, job_payload: str, schema: type[Any]) -> Any
async def generate_text(...)
```

## Где используется

- `ExtractionNode` — `RawItem -> JobDraft`
- `LLMRelevanceClassificationNode` — late relevance gate
- `PresentableTextNode` — presentation payload for delivery
- prompt-building flows, где нужен free-form generation

## Важная архитектурная идея

LLM не должен быть размазан по всему пайплайну хаотично. Текущая архитектура
держит LLM-точки явными и ограниченными.

До LLM идут дешёвые ворота:

- sanitize
- garbage/post-type filters
- hard filters
- dedup
- semantic prefilters

Это уменьшает стоимость и latency.

## Реализации

В репозитории есть, как минимум:

- OpenAI-based provider
- heuristic provider
- fastembed / reranker-adjacent integrations для соседних scoring paths

Точная зрелость конкретной реализации зависит от extras и runtime settings.

## Что не делать

- не добавлять LLM-вызовы в случайные nodes без новой явной архитектурной причины
- не возвращать объект в обход schema validation
- не смешивать transport concerns и business decisions выше уровня provider

## Связанные документы

- [JobDraft](job_draft.md)
- [JobRecord](job_record.md)
- [AuthProvider](auth_provider.md)
- [Protocols](protocols.md)
