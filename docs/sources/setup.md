---
title: "Source Setup"
description: "Prefer declarative source setup through `SourceSpec` entries inside a tenant YAML file."
updated: 2026-08-01
---
# Source Setup

Prefer declarative source setup through `SourceSpec` entries inside a tenant YAML file.
Use env-only source selection for quick local runs, not as the main installation shape.

---

## Default CIS source fixture

For searching AI engineers, vibe-coders, AI automation engineers, AI product builders,
and AI managers across Russia and Kazakhstan, use the canonical fixture:

**`fixtures/sources/ai_jobs.json`** — 17 sources (5 Telegram/group + 12 career-site bare entry URLs),
committed to the repository. See [Source Coverage Matrix](coverage_matrix.md#default-fixture--ai-engineers--vibe-coders--ai-automation--ai-managers-cis)
for the full table and a smoke-test recipe.

```python
from pathlib import Path
from job_ftch.application.source_loader import load_sources

sources = load_sources(Path("fixtures/sources/ai_jobs.json"))
```

This is the authoritative starting point for:
- eval runs that target the AI/LLM engineering market in CIS
- ingest smoke tests before pushing to production
- manual pipeline verification after changes to the relevance or extraction layer

Career-site entries are intentionally bare per host/path. Runtime expansion
generates per-role search URLs on each run instead of storing stale `?text=`,
`?q=` or `?keywords=` queries in the fixture.

For another profession, do not reuse this source/profile recipe as-is. Build a
profession-specific tenant onboarding package first:

1. Minimum profile shots: 12 negative resume shots, 12 positive resume shots,
   12 positive vacancy shots, and 12 negative vacancy shots.
2. A labelled dataset for that profession with `relevant=1/0`.
3. A trained profile-specific prefilter artifact from
   `scripts/eval/train_relevance_prefilter.py`.
4. Hold-out/live eval evidence that recall is acceptable.

Until that exists, disable `tfidf_logreg_prefilter` for the new profession or
run it only as an experiment with publishing disabled.


---

## Bootstrap flow

For a complete one-shot dev environment bootstrap (including Telegram bot config, test user registration, and DB prep), use the provided dev fixtures:

1. Copy the ready-made tenant config:
   `cp fixtures/bootstrap/tenant_ai_jobs.yaml job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml`
2. Set `.env.dev` credentials (OpenAI key, Telegram bot token, API ID/hash).
3. Run the bootstrap script to seed Qdrant with shots:
   `uv run python scripts/bootstrap_dev.py`
4. Start the bot:
   `uv run python job_ftch/adapters/telegram_bot/main.py`

See `fixtures/bootstrap/README.md` for more details.

---

## Telegram sources

Runtime prerequisites:

1. Fill repo-root `.env.dev`.
2. Review `config/runtime.yaml` and `config/runtime.dev.yaml`.
3. Fill bot-specific `job_ftch/adapters/telegram_bot/.env.dev`.
4. Ensure `JOB_FTCH_TELEGRAM_API_ID`, `JOB_FTCH_TELEGRAM_API_HASH`, and `JOB_FTCH_TELEGRAM_SESSION_PATH` are set for the reader session.
5. Ensure `JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN` is set for the bot runtime.
6. Start with public entities first (`ai_jobs`, `@ai_jobs`) before moving to private or invite-only targets.

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

- `telegram_comment_post_limit`
- `telegram_comment_limit_per_post`
- `telegram_history_wait_time_seconds`

Set them in `config/runtime.yaml` or `config/runtime.dev.yaml`.

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
2. Keep domain-specific allowlists and parser policy in source/runtime YAML, not in env.
3. Tune timeout/retry policy through `config/runtime*.yaml` only after confirming a real reliability issue.

Supported current patterns are still centered around lightweight HTML flows:

- Greenhouse boards through parser auto-detection
- bounded detail-page concurrency for selected boards
- site-specific adapters only when declarative extraction is not enough

For crawler/scraper/parser/monitor responsibilities, see
[Ingest stack](ingest_stack.md). For source capability classification before
ingest, see [Source assessment](source_assessment.md). For access failures,
browser requirements and proxy/bypass escalation, see
[Bypass and escalation](bypass_and_escalation.md).

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

Career-site ingest eval over the curated CIS URL fixture:

```bash
uv run python scripts/run_ingest_batch.py \
  --input fixtures/sources/career_sites_cis_303.yaml \
  --out-json .runtime/runs/ingest_batch_303_direct_urls.json \
  --timeout 120 \
  --hard-cancel-grace 15 \
  --max-items 1 \
  --concurrency 10 \
  --gate \
  --min-success-rate 0.65
```

`scripts/run_ingest_batch.py` is the only supported test ingest eval runner for
URL fixture batches. It probes URLs independently under a bounded semaphore,
with an isolated hard deadline per URL and incremental checkpoints. The coverage
gate intentionally uses one item per URL: it measures whether the source can
produce vacancies, not whether it can exhaustively scrape every detail page in a
single bounded run. Use larger `--max-items` values only for diagnostics after a
source is already known to parse.

Ingest eval contract:

- Success is `parse_status == "parsed_ok"`: the source yielded at least one item.
- The 303-source coverage gate is `parsed_ok / total >= 0.65`.
- `parsed_partial` means the source yielded items but did not finish cleanly; keep it visible as a diagnostic outcome, do not count it as `parsed_ok`.
- `parsed_failed` with `failure_bucket in {"protected", "no_open_vacancies", "board_gone"}` is usually an access/content outcome, not necessarily a parser regression.
- `timeout_global` means the task watchdog recorded a stalled source task. Investigate if this is frequent; a small count can happen on browser-backed sites during local runs.

Operational notes:

- `--max-items` defaults to `1` and should stay `1` for the coverage gate.
- `--gate --min-success-rate 0.65` makes the eval fail non-zero when coverage
  drops below the release floor.
- `--timeout` is an isolated per-URL source budget.
- `--hard-cancel-grace` is the watchdog grace after `--timeout`; after that the URL is recorded as `timeout_global` and the run continues.
- `--resume` skips URLs already present in `--out-json` and preserves fixture order, so interrupted runs can be safely continued.
- `--soft-timeout` and `--overflow-concurrency` are accepted for CLI compatibility only; this eval runner does not use the production dynamic overflow scheduler.
- `--slow-queue-out` is diagnostic only; the canonical coverage run should complete through the single command plus optional `--resume`.

If a run is interrupted or a terminal/browser task stalls, resume it:

```bash
uv run python scripts/run_ingest_batch.py \
  --input fixtures/sources/career_sites_cis_303.yaml \
  --out-json .runtime/runs/ingest_batch_303_direct_urls.json \
  --resume \
  --timeout 120 \
  --hard-cancel-grace 15 \
  --max-items 1 \
  --concurrency 10 \
  --gate \
  --min-success-rate 0.65
```

Current local validation after switching the runner to isolated URL probes:

| Metric | Value |
|---|---:|
| Total URLs | `303` |
| Unique URLs | `303` |
| `parsed_ok` | `215` |
| Success rate | `70.96%` |
| Artifact | `.runtime/runs/ingest_batch_303_direct_urls.json` |

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
cat > /tmp/tg-tenant.yaml <<'EOF'
tenant_id: tg_smoke
display_name: Telegram Smoke Test
sources:
  - type: telegram_channel
    entity: ai_jobs
    limit: 20
output:
  backend: json_file
  path: artifacts/tg_smoke/jobs.json
  jsonl: false
  schema_version: job_ftch.job_record.v1
EOF
uv run job_ftch run --config /tmp/tg-tenant.yaml
```

Legacy quick-run career site:

```bash
cat > /tmp/cs-tenant.yaml <<'EOF'
tenant_id: cs_smoke
display_name: Career Site Smoke Test
sources:
  - type: career_site
    url: https://job-boards.greenhouse.io/clickhouse
output:
  backend: json_file
  path: artifacts/cs_smoke/jobs.json
  jsonl: false
  schema_version: job_ftch.job_record.v1
EOF
uv run job_ftch run --config /tmp/cs-tenant.yaml
```

> `uv run job_ftch run` is the entry point installed by `uv sync`. If you
> prefer the Python API: `from job_ftch.application.builder import run`.
