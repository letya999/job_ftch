# FastAPI adapter (scaffold)

Integration adapter that mounts `job_ftch` onto a FastAPI app. This is a
**scaffold**, not the MVP deploy — it needs a configured `PipelineBuilder`.

## Usage

```python
import uvicorn
from job_ftch import PipelineBuilder
from job_ftch.domain.source_spec import LocalFixtureSpec
from job_ftch.nodes import SanitizeNode
from adapters.fastapi.adapter import create_app

builder = PipelineBuilder().source(LocalFixtureSpec(path="fixtures/e2e/multisource_positive.jsonl"))
builder.stage(SanitizeNode())
app = create_app(builder)  # ASGI app

uvicorn.run(app, host="0.0.0.0", port=8080)
```

## Build

```bash
docker build -f adapters/fastapi/Dockerfile -t job-ftch-fastapi .
```

Requires the `api` extra (`fastapi`, `uvicorn`).
