# Dagster adapter (scaffnew)

Exposes pipeline sources as Dagster assets. This is a **scaffnew**, not the MVP
deploy — it needs a configured `PipelineBuilder` and a source list.

## Usage

```python
# composition module, e.g. adapters/dagster/defs.py
from job_ftch import PipelineBuilder
from job_ftch.domain.source_spec import LocalFixtureSpec
from job_ftch.adapters.dagster.adapter import create_definitions

specs = [LocalFixtureSpec(path="fixtures/e2e/multisource_positive.jsonl")]
defs = create_definitions(PipelineBuilder(), specs)
# dagster dev -m adapters.dagster.defs
```

## Build

```bash
docker build -f adapters/dagster/Dockerfile -t job-ftch-dagster .
```

Requires the `dagster` extra.
