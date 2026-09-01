---
title: "Scorer/Normalizer Plugin Template"
description: "Template for a custom Scorer/Normalizer plugin."
updated: 2026-07-24
---
# Scorer/Normalizer Plugin Template

Template for a custom Scorer/Normalizer plugin.

## Implementation example

```python
from job_ftch.application.contracts import Stage, PluginMetadata
from job_ftch.domain import JobRecord


class MyScorer(Stage[JobRecord, JobRecord]):
    async def process(self, item: JobRecord) -> JobRecord | None:
        # Custom scoring logic
        score = 0.8
        return item.model_copy(update={"quality_score": score})


metadata = PluginMetadata(
    name="my_scorer",
    version="1.0.0",
    plugin_type="scorer",
    description="Custom quality scoring",
    entry_point_group="job_ftch.scorers",
)
```

## Registration in pyproject.toml

```toml
[project.entry-points."job_ftch.scorers"]
my_scorer = "my_plugin_package.nodes:MyScorer"
```
