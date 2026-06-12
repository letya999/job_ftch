# Sink Plugin Template

Template for a custom Sink plugin.

## Implementation example

```python
from job_ftch.application.contracts import Sink, PluginMetadata
from job_ftch.application.registry import register_sink
from job_ftch.domain import JobRecord

@register_sink("my_custom_sink")
class MySink(Sink[JobRecord]):
    async def emit(self, item: JobRecord) -> None:
        # Send job to external system
        print(f"Sending job {item.job_id} to my system")

    async def flush(self) -> None:
        # Optional: finalize buffered writes
        pass

metadata = PluginMetadata(
    name="my_custom_sink",
    version="1.0.0",
    plugin_type="sink",
    description="Sends jobs to My System",
    entry_point_group="job_ftch.sinks"
)
```

## Registration in pyproject.toml

```toml
[project.entry-points."job_ftch.sinks"]
my_sink = "my_plugin_package.sink:MySink"
```
