---
title: "Dagster adapter"
description: "Scaffold adapter: exposes configured sources as Dagster definitions."
updated: 2026-07-28
---
# Dagster adapter

`job_ftch.adapters.dagster` — scaffold adapter, not the MVP deployment path. It
wraps a configured `PipelineBuilder` and explicit `SourceSpec` list into
Dagster definitions.

## Source of truth

- Code: `job_ftch/adapters/dagster/adapter.py`
- Local README: `job_ftch/adapters/dagster/README.md`
- Dockerfile: `job_ftch/adapters/dagster/Dockerfile`

## Minimal usage

```python
from job_ftch import PipelineBuilder
from job_ftch.adapters.dagster.adapter import create_definitions
from job_ftch.domain.source_spec import LocalFixtureSpec

specs = [LocalFixtureSpec(path="fixtures/e2e/multisource_positive.jsonl")]
defs = create_definitions(PipelineBuilder(), specs)
```

## Boundary

Dagster owns orchestration around assets. `job_ftch` still owns source
contracts, pipeline stages, stores and decisions.
