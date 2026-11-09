<p align="center"><img src=".github/job-ftch-banner.svg" alt="JobFetch: collect, extract, evaluate, publish" width="100%"></p>

<h1 align="center">JobFetch</h1>
<p align="center"><strong>Turn scattered job signals into vacancies worth reading.</strong></p>
<p align="center"><a href="https://job-ftch.aiguild.space/">Live site</a> · <a href="https://t.me/ai_engineer_jobs">Telegram feed</a> · <a href="https://letya999.github.io/job_ftch/">Documentation</a> · <a href="README.ru.md">Русский</a></p>
<p align="center">
  <a href="https://github.com/letya999/job_ftch/actions/workflows/ci.yml"><img src="https://github.com/letya999/job_ftch/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/letya999/job_ftch/releases"><img src="https://img.shields.io/github/v/release/letya999/job_ftch" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB" alt="Python 3.12 or newer">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--only-E67E45" alt="AGPL-3.0-only license"></a>
</p>

JobFetch is a self-hostable, asynchronous pipeline for collecting job posts from Telegram, career sites, RSS and APIs. It turns uneven source content into typed vacancy records, groups duplicates, evaluates relevance and sends accepted results to a feed or another sink. The public site shows vacancies and source status.

It is for communities and teams that want to **own the ingest and publishing pipeline** rather than copy listings by hand.

## See it work

- [Browse vacancies](https://job-ftch.aiguild.space/ru/jobs) and [inspect sources](https://job-ftch.aiguild.space/ru/sources).
- Read the [Telegram vacancy feed](https://t.me/ai_engineer_jobs).
- Explore the [architecture](docs/architecture.md) and [source setup](docs/sources/setup.md).

```text
Telegram · career sites · RSS · APIs
                  ↓
       collect → extract → normalize
                  ↓
      deduplicate → evaluate relevance
                  ↓
         publish → site / Telegram / sink
```

Each stage has an explicit contract. The pipeline keeps source evidence and separates ingestion, decisions and delivery; see the [architecture guide](docs/architecture.md) for the runtime boundaries.

## Try it locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/letya999/job_ftch.git
cd job_ftch
uv sync
uv run job_ftch eval --fixture classification
```

This evaluates a committed local fixture; it does not contact live sources or publish anything. For a configured tenant, follow the [quickstart](docs/quickstart.md), then run `uv run job_ftch validate --config tenant.yaml` before `uv run job_ftch run --config tenant.yaml`.

## What is inside

| Part | Role |
| --- | --- |
| Source adapters | Telegram, career sites, RSS, API and local-fixture inputs |
| Typed pipeline | Sanitation, extraction, normalization, grouping and evidence-based decisions |
| Delivery | Telegram bot, file/review outputs and other sinks |
| Runtime | Tenant isolation, source overlays, CLI, MCP and API adapters |
| Operations | PostgreSQL-backed state, diagnostics and OpenObserve integration |

The Telegram bot is the primary production deployment. Other adapters have their own maturity levels; see [runtime and environment](docs/adapters/runtime_and_env.md) and [quality gates](docs/operations/ci-cd.md). Site parsing depends on upstream pages and is checked source by source; green CI does not prove every live job board works.

## Choose an LLM route

Start with a token and an OpenAI-compatible endpoint. Additional services are optional:

| Mode | Requirements | Behavior |
| --- | --- | --- |
| Direct compatible API | Endpoint, model and API token | Inline calls; no dispatch queue or phone required |
| Queued compatible API | PostgreSQL, worker and `llm-queue` extra | Durable requests, shared budgets and deferred continuation |
| Protected CLIProxyAPI | CLIProxyAPI profile with `gateway: cliproxy` and `execution: queued` | Run affinity, jitter, classified cooldowns, circuit breaker and optional completion report |
| CLIProxyAPI with home egress | Protected CLIProxyAPI plus Tailscale and an Android exit node | Required traffic uses the pinned phone; an unavailable exit pauses dispatch |

Queue state lives in PostgreSQL; Redis is not required. A logical session lasts for one run and its deferred continuations, including restarts. Each vacancy keeps its own prompt and request identity. Shared affinity does not combine unrelated conversations. A direct profile does not inherit CLIProxy-specific behavior.

Follow the [queue and home egress runbook](docs/operations/llm-queue-and-home-egress.md) for configuration, Android setup, status commands and recovery. Local pacing cannot increase provider quotas or guarantee protection from account restrictions.

CLIProxy continuations replay immutable observations between scheduled source collections. Safe unfinished runs renew their lease, and one waiting request does not block independent vacancies from entering the bounded queue. Fresh collection continues on the tenant schedule while older observations drain first. Accepted publication candidates survive timestamp windows and restarts; the target ledger prevents duplicate delivery. Reports distinguish attempt results, new candidates and deferred work.

## Operate and recover

- [Production deployment](docs/deploy.md): containers, durable state and readiness checks.
- [LLM dispatch](docs/operations/llm-queue-and-home-egress.md): inspect pending work, cooldowns and operator holds.
- [Publication recovery](docs/deploy.md#сверка-неопределённой-публикации): reconcile a lost Telegram response before resending a card.
- [Release checklist](docs/release_checklist.md): tests, secret scanning, CI and deployment parity.

Keep API tokens, OAuth credentials, Telegram sessions and Tailscale state outside Git and image build contexts. Back up the database and private state before updating. Preserve the active Compose overlays during upgrades: starting only the base stack does not retain an optional queue or home route.

An unknown LLM outcome and an unknown Telegram delivery are separate holds. Restoring the model route does not confirm whether a card was delivered. Use the matching recovery procedure; the request store and publication ledger prevent automatic blind replays.

## Check a change

```bash
just code-verify
just tests-all
just docs-verify
```

These commands cover code/type checks, the non-network test suite and documentation. Queue integration tests require a disposable PostgreSQL database; CI supplies one. See [CI/CD](docs/operations/ci-cd.md) for optional extras, browser checks and the full release gate.

## Other projects in this space

These are independent examples of related approaches, not components of JobFetch:

| Project | Main focus |
| --- | --- |
| [Job Seek](https://github.com/colophon-group/jobseek) | Search and tracking built on company career pages and ATS feeds |
| [freehire](https://github.com/strelov1/freehire) | Open-source, self-hostable tech-job search and aggregation |
| [JobSpy](https://github.com/speedyapply/JobSpy) | Python library for scraping major job boards |
| [career-ops](https://github.com/career-ops-hq/career-ops) | Local candidate-side agent for job evaluation, CV work and application tracking |

JobFetch focuses on the configurable **source → evidence → relevance → publication** path, especially for a community-operated Telegram feed. These projects solve overlapping but different parts of the job-search workflow.

## Develop and contribute

Start with the [vision](docs/vision.md), [architecture](docs/architecture.md), [contributing guide](.github/CONTRIBUTING.md) and [security policy](SECURITY.md). Issues are welcome for reproducible source failures and pipeline defects. Licensed under [AGPL-3.0-only](LICENSE).
