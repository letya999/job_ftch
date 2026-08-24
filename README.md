---
title: "job_ftch"
description: "![Python](https://img.shields.io/badge/python-3.12+-blue.svg)"
updated: 2026-08-25
---
# job_ftch

<p align="center"><img src="job_ftch_site/public/brand/job-ftch-icon.png" alt="job_ftch project icon" width="160" /></p>

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-MVP-orange.svg)

[Сайт документации](https://letya999.github.io/job_ftch/) · [Telegram-канал вакансий](https://t.me/ai_engineer_jobs) · [AI Engineers Guild](https://t.me/ai_engineers_guild) · [GitHub](https://github.com/letya999/job_ftch)

**Async pipeline for collecting vacancies from Telegram, career sites, RSS,
selected APIs, and runtime overlays into structured, deduplicated,
profile-scored `JobRecord` items.**

## What it is

`job_ftch` is a library-first async ETL core.

It ingests job-like postings from multiple source types, normalizes them into a
canonical contract, drops garbage and duplicates early, scores them against one
or more search profiles, and writes accepted or reviewable records to sinks.

The durable spine of the system is:

`Source -> RawItem -> JobDraft -> JobRecord -> JobGroup`

The repo also contains runtime adapters around the core:

- CLI
- Telegram bot
- MCP server
- FastAPI bridge
- FastStream and Dagster wrappers for secondary runtime surfaces

## What it is not

- Not a queue/orchestrator replacement
- Not a generic web crawler
- Not a "single source scraper in one file"
- Not a realtime-by-default event streaming platform

Several source specs for push or realtime ingestion exist today, but some of
them are still adapter-oriented or stub-level rather than production-complete.

## Quick start

```bash
git clone https://github.com/letya999/job_ftch
cd job_ftch
uv sync
```

Run on a local fixture:

```yaml
# tenant.yaml
tenant_id: smoke
display_name: Smoke Test
sources:
  - type: local_fixture
    path: fixtures/e2e/multisource_positive.jsonl
    source_name: smoke_fixture
```

```bash
uv run job_ftch run --config tenant.yaml
```

Useful outputs are written under `artifacts/` according to the tenant settings.

The default production recipe is tuned for the `ai_jobs` / AI-engineering
profile. For any other profession, do not run the default hard
`tfidf_logreg_prefilter` as production policy until you have built and labelled
a profession-specific dataset and trained a matching prefilter artifact. A new
profile must also start with at least 48 shots: 12 negative resume shots, 12
positive resume shots, 12 positive vacancy shots, and 12 negative vacancy
shots. Until then, use a no-prefilter or experimental run without publishing.

## Current pipeline

The project is split into small layers. Data enters through sources and runtime
adapters, passes through the application pipeline, and leaves through sinks.
The detailed processing order lives in
[`docs/architecture.md`](docs/architecture.md).

![job_ftch architecture layers](docs/architecture.svg)

The main data contract is:

`Source -> RawItem -> JobDraft -> JobRecord -> JobGroup -> Sink`

Important invariants:

- `SanitizeNode` is always first.
- `SnapshotFilterNode`, when enabled, is always second.
- `ExtractionNode` is the raw-to-structured boundary.
- `EvidenceDecisionNode` is the only terminal runtime decision boundary.
- `Stage[In, Out]` is the only supported type-changing mechanism in core.
- `domain/` imports only stdlib and `pydantic`.

## Core architecture

The project follows a strict layered model:

- `job_ftch/domain/` - pure models and value objects
- `job_ftch/application/` - protocols, orchestration, composition roots
- `job_ftch/nodes/` - pipeline stages
- `job_ftch/sinks/` - output sinks
- `job_ftch/infrastructure/` - adapters for LLMs, stores, sources, embeddings
- `adapters/` - runtime wrappers around the public library API

The primary protocols live in `job_ftch/application/contracts.py`:

- `Source[T]`
- `Stage[In, Out]`
- `Sink[T]`
- `Store`
- `AuthProvider`
- `LLMProvider`

Additional ports cover search, vectors, auth, ingestion modes, bypass
strategies, metrics, and job persistence.

## CLI

Preferred commands:

```bash
uv run job_ftch run --config tenant.yaml
uv run job_ftch validate --config tenant.yaml
uv run job_ftch eval --fixture classification
```

Other supported commands:

- `search`
- `runs list|show`
- `tenants list|status|lineage|run|reset`
- `mcp-server`
- `telegram-bot`
- legacy `pipeline` mode and legacy top-level flags

## Source support

Status below reflects the current repository state rather than roadmap intent.

| Source type | Status | Notes |
|---|---|---|
| `telegram_channel` / `telegram_group` / `telegram_comment` | Stable | Polling via Telethon |
| `career_site` | Stable | Monitor + scraper chain, cached strategies, bypass escalation |
| `declarative_html` | Stable | Declarative extraction path |
| `local_fixture` | Stable | Dev and tests |
| `rss_feed` | Stable | Polling feed source |
| `rest_api` / `greenhouse_api` / `hh_api` / `lever` | Experimental | API-specific adapters vary by maturity |
| `browser` | Stub | Registered source spec, not a production scraper by itself |
| `telegram_realtime` | Experimental | Long-running realtime source |
| `webhook` / `websocket` | Stub / adapter-oriented | Specs exist; builtin source implementations are not full ingestion backends |

## Sinks and side channels

Main outputs are written through sinks. Current builtins include:

- `JsonFileSink`
- `NullSink`
- sink wrappers such as fan-out, buffering, failure-tolerant, and counted sinks
- Telegram posting sink via adapter path

The pipeline also supports side channels for:

- quarantine
- rejected items
- review outputs

## Multi-tenant runtime

`TenantRunner` builds isolated runtimes per tenant:

- merges base sources with runtime source overlays
- applies source health and rate-limit decisions
- loads candidate profiles and runtime ontology
- stores run summaries, snapshots, and dedup state in namespaced storage

This is the recommended execution model for long-lived deployments.

## Observability

OpenTelemetry is wired into the pipeline. Langfuse integration is documented in
ADR-043 and the runtime docs.

Useful settings:

- `JOB_FTCH_TRACING_ENABLED`
- `JOB_FTCH_TRACING_CAPTURE_PAYLOADS`
- standard OTLP environment variables

## Documentation

| Topic | File |
|---|---|
| Vision | [docs/vision.md](docs/vision.md) |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Filtering pipeline | [docs/pipelines/filtering_pipeline.md](docs/pipelines/filtering_pipeline.md) |
| Production recipe | [docs/recipes/pipeline_recipe.md](docs/recipes/pipeline_recipe.md) — рецепт воспроизводимости: тестовый пользователь, тенант, 17 источников, датасеты, граф, настройки и regression gates |
| Ontology compiler | [docs/ontology/compiler.md](docs/ontology/compiler.md) |
| Tech stack | [docs/tech_stack.md](docs/tech_stack.md) |
| Runtime and env | [docs/adapters/runtime_and_env.md](docs/adapters/runtime_and_env.md) |
| Quality gates and CI | [docs/operations/ci-cd.md](docs/operations/ci-cd.md) |
| Release checklist | [docs/release_checklist.md](docs/release_checklist.md) |
| Source setup | [docs/sources/setup.md](docs/sources/setup.md) |
| Examples | [docs/examples.md](docs/examples.md) |
| Entity map | [docs/entities/README.md](docs/entities/README.md) |
| Domain model map | [docs/entities/domain_model_map.md](docs/entities/domain_model_map.md) |
| Node catalog | [docs/nodes/README.md](docs/nodes/README.md) |
| ADR index | [docs/adr/README.md](docs/adr/README.md) |

## License

MIT. See [LICENSE](LICENSE).
