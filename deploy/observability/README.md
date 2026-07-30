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
