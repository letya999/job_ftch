# Configuration

`job_ftch` currently has two configuration layers:

1. Preferred runtime configuration: `TenantConfig` in YAML or JSON.
2. Legacy quick-run compatibility: environment variables and one-off CLI flags.

If you are setting up a real installation, prefer tenant files plus declarative `SourceSpec`.
Use env-first runs only for debugging, local experiments, or backward-compatible wrappers.

---

## Preferred model

The main entry point is a tenant config file loaded through the Python API `job_ftch.configure(...)`.
For CLI multi-tenant operation, the current entry point is `--configs-dir` with one or more tenant files.

Typical structure:

```yaml
tenant_id: ai_jobs
display_name: AI Jobs

sources:
  - type: telegram_channel
    entity: ai_jobs
    limit: 100
  - type: career_site
    url: https://job-boards.greenhouse.io/clickhouse

output:
  backend: json_file
  path: artifacts/{tenant_id}/jobs.json
  jsonl: false
  schema_version: job_ftch.job_record.v1

quarantine_output:
  path: artifacts/{tenant_id}/quarantine.jsonl
  jsonl: true

review_output:
  path: artifacts/{tenant_id}/review.jsonl
  jsonl: true

rejected_output:
  path: artifacts/{tenant_id}/rejected.jsonl
  jsonl: true

schedule:
  interval_seconds: 900

metrics_enabled: true
metrics_port: 9090
```

Key ideas:

- `tenant_id` is the namespace root for paths, store keys, and runtime isolation.
- `sources` is a list of `SourceSpec` entries. This is the preferred way to describe what to fetch.
- Secrets do not live in the tenant file. Runtime credentials are resolved through `AuthProvider`.
- Output blocks define where canonical `JobRecord` items and side channels are written.
- `metrics_enabled` and `metrics_port` enable the Prometheus exporter for tenant runs.

Python API example:

```python
import asyncio
from pathlib import Path

from job_ftch.application.builder import configure

builder = configure(Path("config/tenant.yaml"))
summary = asyncio.run(builder.run_async())
```

---

## SourceSpec

`SourceSpec` is the declarative source contract used by the registry and builder layer.

Examples:

Telegram channel:

```yaml
- type: telegram_channel
  entity: ai_jobs
  limit: 100
```

Telegram group:

```yaml
- type: telegram_group
  entity: data_jobs_chat
  limit: 200
```

Career site:

```yaml
- type: career_site
  url: https://job-boards.greenhouse.io/clickhouse
```

The exact schema is exported to `config/sources.schema.json`.

---

## Auth and secrets

Secrets are runtime-only:

- Telegram: `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, `JOB_FTCH_TELEGRAM_SESSION_PATH`
- OpenAI: `JOB_FTCH_OPENAI_API_KEY`
- Qdrant: `JOB_FTCH_QDRANT_API_KEY`
- Other backends: env or provider-specific secret resolution

Rules:

- Never commit credentials inside `TenantConfig` or `SourceSpec`.
- Prefer `EnvAuthProvider` for local/dev.
- Use file- or vault-based auth providers only when you actually need them operationally.

---

## Important environment variables

Even in the preferred tenant/YAML flow, some environment variables remain important because they
control infrastructure and secrets rather than source shape.

Core:

- `JOB_FTCH_STORE_BACKEND`
- `JOB_FTCH_LLM_BACKEND`
- `JOB_FTCH_JOB_BACKEND`
- `JOB_FTCH_SEARCH_BACKEND`
- `JOB_FTCH_VECTOR_BACKEND`

Pipeline guards:

- `JOB_FTCH_PIPELINE_MAX_ITEMS_PER_RUN`
- `JOB_FTCH_PIPELINE_MAX_TEXT_LENGTH`

Telegram:

- `JOB_FTCH_TELEGRAM_API_ID`
- `JOB_FTCH_TELEGRAM_API_HASH`
- `JOB_FTCH_TELEGRAM_SESSION_PATH`

OpenAI extraction:

- `JOB_FTCH_OPENAI_API_KEY`
- `JOB_FTCH_OPENAI_MODEL`
- `JOB_FTCH_OPENAI_BASE_URL`
- `JOB_FTCH_OPENAI_TIMEOUT_SECONDS`
- `JOB_FTCH_OPENAI_MAX_RETRIES`

Search and vector:

- `JOB_FTCH_EMBEDDING_ENABLED`
- `JOB_FTCH_EMBEDDING_PROVIDER`
- `JOB_FTCH_EMBEDDING_MODEL`
- `JOB_FTCH_QDRANT_URL`
- `JOB_FTCH_QDRANT_COLLECTION`
- `JOB_FTCH_OLLAMA_BASE_URL`

NLP retrieval quality (all default to `false`/off):

- `JOB_FTCH_LANGUAGE_DETECTION_ENABLED` — enable `LanguageDetectionNode`; requires `[language]` extras
- `JOB_FTCH_TRANSLATION_ENABLED` — enable `TranslationNode` (RU↔EN only); requires `[translation]` extras
- `JOB_FTCH_TRANSLATION_TARGET_LANGUAGE` — target language for translation (default: `ru`)
- `JOB_FTCH_RERANKER_ENABLED` — enable cross-encoder reranking in `/digest`; requires `[fastembed]`
- `JOB_FTCH_RERANKER_MODEL` — reranker model key (default: `jina-v2-multilingual`)
- `JOB_FTCH_RERANKER_TOP_K` — candidates fetched before reranking (default: `50`)

Outputs:

- `JOB_FTCH_OUTPUT_PATH`
- `JOB_FTCH_REVIEW_OUTPUT_PATH`
- `JOB_FTCH_REJECTED_OUTPUT_PATH`
- `JOB_FTCH_QUARANTINE_OUTPUT_PATH`
- `JOB_FTCH_REVIEW_MAX_QUALITY_SCORE`
- `JOB_FTCH_POSTING_MIN_QUALITY_SCORE`

Metrics:

- `JOB_FTCH_METRICS_ENABLED`
- `JOB_FTCH_METRICS_PORT`

## Enable Telegram notifications

To send discovered jobs to a Telegram channel or group, set the following environment variables:

```bash
JOB_FTCH_POSTING_BACKEND=telegram_posting
JOB_FTCH_TELEGRAM_PUBLISH_ENTITY=@my_channel   # or numeric channel id
JOB_FTCH_POSTING_MIN_QUALITY_SCORE=0.75         # optional, default 0.8
```

Telegram posting fires per-job when `job.quality_score >= posting_min_quality_score`.

Alternatively, you can use the bot command `/setposting <tenant_id> <channel>` to enable it at runtime.

---

## Legacy quick-run mode

For one-off runs, the old env/CLI flow still works.

Fixture run:

```bash
uv run python app.py \
  --source-path fixtures/e2e/multisource_positive.jsonl \
  --output-path artifacts/debug/jobs.json \
  --max-items 20
```

One-off Telegram run:

```bash
uv run python app.py \
  --source-backend telegram_channel \
  --telegram-entity ai_jobs \
  --max-items 100
```

One-off career-site run:

```bash
uv run python app.py \
  --source-backend career_site \
  --career-site-url https://job-boards.greenhouse.io/clickhouse \
  --max-items 20
```

Multi-tenant CLI example:

```bash
uv run python app.py tenants list --configs-dir config/tenants
```

Treat env/flag-only runs as a compatibility shim around the newer builder/config system, not as the long-term primary contract.
