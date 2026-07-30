---
title: "FastStream adapter"
description: "Scaffold message-queue adapter around a configured PipelineBuilder."
updated: 2026-07-28
---
# FastStream adapter

`job_ftch.adapters.faststream` registers queue handlers that trigger a
configured pipeline and publish results to another subject.

## Source of truth

- Code: `job_ftch/adapters/faststream/adapter.py`
- Local README: `job_ftch/adapters/faststream/README.md`
- Dockerfile: `job_ftch/adapters/faststream/Dockerfile`

## Minimal usage

```python
from faststream import FastStream
from faststream.nats import NatsBroker
from job_ftch import PipelineBuilder
from job_ftch.adapters.faststream.adapter import register_faststream_handlers

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
register_faststream_handlers(app, broker, PipelineBuilder())
```

## Boundary

FastStream owns broker integration. `job_ftch` still owns source specs,
pipeline execution and result semantics.
