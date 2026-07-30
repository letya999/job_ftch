---
title: "Sink"
description: "`Sink` — финальная точка назначения для pipeline output."
updated: 2026-07-24
---
# Sink

## Что это

`Sink` — финальная точка назначения для pipeline output.

Базовый контракт:

```python
async def emit(self, item: T) -> None
```

На практике чаще всего `T = JobRecord`, но обобщение сохранено.

## Что считается текущим sink stack

В repo есть:

- `JsonFileSink`
- `NullSink`
- `FanOutSink`
- `BufferingSink`
- `CountedSink`
- `FailureTolerantSink`
- routing-related sink composition
- Telegram posting path через adapter/backend integration

## Важное поведение

- Sink получает уже финальный item после routing.
- `FlushableSink.flush()` вызывается оркестратором в конце run.
- Sinks не должны переписывать весь output file на каждый `emit`.

## Side channels

Кроме main sink существуют отдельные каналы:

- quarantine sink
- rejected sink
- review sink

Это важная часть текущей runtime-модели: не всё, что не попало в main output,
теряется молча.

## Что не делать

- не принимать `JobDraft` как публичный durable output
- не завязывать sink на конкретный source type
- не тащить внутрь sink бизнес-решения про релевантность

## Связанные документы

- [JobRecord](job_record.md)
- [RunSummary](run_summary.md)
- [PipelineBuilder](pipeline_builder.md)
