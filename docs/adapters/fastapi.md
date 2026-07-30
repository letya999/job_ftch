---
title: "FastAPI adapter"
description: "Scaffold ASGI adapter for running/searching jobs through HTTP endpoints."
updated: 2026-07-28
---
# FastAPI adapter

`job_ftch.adapters.fastapi` mounts a configured `PipelineBuilder` into a
FastAPI app. It is a scaffold adapter, not the Telegram bot production stack.

## Source of truth

- Code: `job_ftch/adapters/fastapi/adapter.py`
- Local README: `job_ftch/adapters/fastapi/README.md`
- Dockerfile: `job_ftch/adapters/fastapi/Dockerfile`

## Minimal usage

```python
from job_ftch import PipelineBuilder
from job_ftch.adapters.fastapi.adapter import create_app
from job_ftch.domain.source_spec import LocalFixtureSpec
from job_ftch.nodes.sanitize import SanitizeNode

builder = PipelineBuilder().source(
    LocalFixtureSpec(path="fixtures/e2e/multisource_positive.jsonl")
)
builder.stage(SanitizeNode())
app = create_app(builder)
```

## Endpoints

- `POST /pipeline/run`
- `GET /pipeline/status`
- `GET /jobs/search?q=...`

## Boundary

The adapter exposes HTTP. It does not own tenant loading, graph policy or
production scheduling.
