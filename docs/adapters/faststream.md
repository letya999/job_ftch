# FastStream adapter

```python
from faststream.nats import NatsBroker

from job_ftch import configure
from adapters.faststream.adapter import register_faststream_handlers

broker = NatsBroker("nats://localhost:4222")
builder = configure("config/tenant.yaml")
register_faststream_handlers(
    broker,
    subject="job_ftch.trigger",
    publish_subject="job_ftch.results",
    builder=builder,
)
```
