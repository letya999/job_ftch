# Operational observability

This standalone MVP stack runs OpenObserve OSS as one container. Its embedded
UI and API share port `5080`; SQLite metadata, WAL, and Parquet data persist in
the `openobserve_data` volume.

## Development

1. Copy `.env.dev.example` to `.env.dev` and set a unique root email and password.
2. Start the service from the repository root:

```powershell
docker compose --env-file deploy/observability/.env.dev -f deploy/observability/docker-compose.dev.yml up -d
```

Open <http://127.0.0.1:5080>.

## Dashboards

Import `deploy/observability/dashboards/job_ftch_ingest.json` (Dashboards → Import).
Replace `__LOGS_STREAM__` with the live logs stream if the UI does not substitute it
(`job_ftch_ingest` by default, `job_ftch_bot` on some prod bots).

The bot also upserts this dashboard best-effort when OpenObserve is configured.

Tabs:

- **По прогонам** — latency (wall time), LLM cost, conversion (extract/accept), ok/fail sources, funnel, and per-source status history across runs (`source_run_stats`)
- **Один прогон** — variable `source_run_id`, per-source table, status pie, kinds bar
- **Метки** — four tables (important / reliable / rich / high_relevance) scoped to the selected run (`source_run_id`) to avoid historical duplication across runs

After import, verify:
1. Stream name: ensure `__LOGS_STREAM__` was substituted with the active logs stream (`job_ftch_ingest` or `job_ftch_bot`).
2. Variable `Run` (`source_run_id`): select a completed run ID from the dropdown to inspect that run's source breakdown and label snapshot.
3. Quality boolean types: queries use `quality_* = true OR cast(quality_* as text) IN ('true', '1', 't', 'True')` to remain robust whether OpenObserve ingested the field as boolean or string.

`important` is operator-set (`set_source_important` MCP or
`POST /pipeline/sources/{tenant}/important`). The other three labels are
computed over the last 20 pipeline runs. PostgreSQL tables
`jf_source_operator_flags`, `jf_pipeline_run_stats`, `jf_source_run_stats`
are the authority; OpenObserve charts the same payload from
`pipeline_run_stats` / `source_run_stats` logs.

Local processes send telemetry to `http://127.0.0.1:5080`. Docker Desktop
containers use `http://host.docker.internal:5080`.

## Production

Copy `.env.prod.example` to `.env.prod`, use separate production credentials,
and run:

```powershell
docker compose --env-file deploy/observability/.env.prod -f deploy/observability/docker-compose.prod.yml up -d
```

The dev and prod compose files intentionally use the same project and volume
name, so switching the runtime policy does not orphan observability data. Do
not run both variants concurrently on the same host. Langfuse remains a
separate stack for ML/LLM/RAG traces and evaluation.

## Stop

```powershell
docker compose --env-file deploy/observability/.env.dev -f deploy/observability/docker-compose.dev.yml down
```

The named volume is preserved by `down`. Remove it only as an explicit data
deletion operation.
