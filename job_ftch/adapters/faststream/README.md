# FastStream adapter (scaffnew)

Wraps the pipeline as a message-queue consumer/producer. This is a **scaffnew**,
not the MVP deploy — it needs a broker and a configured `PipelineBuilder`.

## Usage

```python
from faststream import FastStream
from faststream.nats import NatsBroker
from job_ftch import PipelineBuilder
from job_ftch.adapters.faststream.adapter import register_faststream_handlers

broker = NatsBroker("nats://localhost:4222")
app = FastStream(broker)
register_faststream_handlers(app, broker, PipelineBuilder())
# faststream run module:app
```

## Build

```bash
docker build -f adapters/faststream/Dockerfile -t job-ftch-faststream .
```

Requires the `faststream` extra.
