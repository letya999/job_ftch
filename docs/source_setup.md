# Source Setup

Prefer declarative source setup through `SourceSpec` entries inside a tenant YAML file.
Use env-only source selection for quick local runs, not as the main installation shape.

---

## Telegram sources

Runtime prerequisites:

1. Copy `.env.example` to `.env`.
2. Fill `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, and `JOB_FTCH_TELEGRAM_SESSION_PATH`.
3. Start with public entities first (`ai_jobs`, `@ai_jobs`) before moving to private or invite-only targets.

Declarative examples:

Telegram channel:

```yaml
sources:
  - type: telegram_channel
    entity: ai_jobs
    limit: 100
```

Telegram group:

```yaml
sources:
  - type: telegram_group
    entity: data_jobs_chat
    limit: 200
```

Telegram comments:

```yaml
sources:
  - type: telegram_comment
    entity: ai_jobs
    post_limit: 25
    comments_per_post: 50
```

Comment-specific controls remain relevant:

- `JOB_FTCH_TELEGRAM_COMMENT_POST_LIMIT`
- `JOB_FTCH_TELEGRAM_COMMENT_LIMIT_PER_POST`
- `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS`

---

## Career-site sources

Declarative example:

```yaml
sources:
  - type: career_site
    url: https://job-boards.greenhouse.io/clickhouse
```

Operational expectations:

1. Use an HTTPS URL.
2. Keep the host in the allowlist if your runtime setup requires `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS`.
3. Tune timeout/retry env vars only after confirming a real reliability issue.

Supported current patterns are still centered around lightweight HTML flows:

- Greenhouse boards through parser auto-detection
- bounded detail-page concurrency for selected boards
- site-specific adapters only when declarative extraction is not enough

---

## Multi-source tenant example

```yaml
tenant_id: ai_jobs
sources:
  - type: telegram_channel
    entity: ai_jobs
    limit: 100
  - type: career_site
    url: https://job-boards.greenhouse.io/clickhouse
```

This is the preferred way to compose a real installation: multiple `SourceSpec` items in one tenant config.

---

## Recommended first runs

Tenant-config driven through the Python API:

```python
import asyncio
from pathlib import Path

from job_ftch.application.builder import configure

builder = configure(Path("config/tenant.yaml"))
summary = asyncio.run(builder.run_async())
```

Legacy quick-run Telegram:

```bash
uv run python app.py --source-backend telegram_channel --telegram-entity ai_jobs --max-items 20
```

Legacy quick-run career site:

```bash
uv run python app.py --source-backend career_site --career-site-url https://job-boards.greenhouse.io/clickhouse --max-items 20
```
