# FastAPI adapter

```python
from job_ftch import configure
from job_ftch.adapters.fastapi_adapter import create_app

builder = configure("config/tenant.yaml")
app = create_app(builder)
```

Endpoints:

- `POST /pipeline/run`
- `GET /pipeline/status`
- `GET /jobs/search?q=...`
