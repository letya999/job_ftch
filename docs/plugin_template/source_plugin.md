# Source Plugin Template

Template for a custom Source plugin.

## Implementation example

```python
from collections.abc import AsyncIterator
from job_ftch.application.contracts import Source, PluginMetadata
from job_ftch.application.registry import register_source
from job_ftch.domain import RawItem, SourceKind, QuarantinedRawItem
from job_ftch.domain.source_spec import SourceSpec

@register_source("my_custom_source")
class MySource(Source[RawItem]):
    def __init__(self, spec: SourceSpec):
        self.spec = spec
        # spec.config contains parameters from yaml

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        # Implementation of data fetching
        yield RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name="my_source",
            external_id="123",
            text="Job description here",
            url="https://example.com/job/123"
        )

# Metadata (optional but recommended)
metadata = PluginMetadata(
    name="my_custom_source",
    version="1.0.0",
    plugin_type="source",
    description="Fetches jobs from My Custom Source API",
    entry_point_group="job_ftch.sources"
)
```

## Registration in pyproject.toml

```toml
[project.entry-points."job_ftch.sources"]
my_source = "my_plugin_package.source:MySource"
```

## Contract Test

```python
from job_ftch.domain import RawItem
from tests.test_plugin_contracts import TestSourcePluginContract

class TestMySourceContract(TestSourcePluginContract):
    def get_source(self):
        return MySource(spec=...) # provide mock/test spec
```
