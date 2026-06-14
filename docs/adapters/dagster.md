# Dagster adapter

```python
from job_ftch import configure
from adapters.dagster.adapter import create_definitions
from job_ftch.domain.source_spec import LocalFixtureSpec

builder = configure("config/tenant.yaml")
defs = create_definitions(
    builder,
    [LocalFixtureSpec(path="fixtures/debug/raw_items.json")],
)
```
