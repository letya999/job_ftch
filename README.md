# job_ftch

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-early%20development-orange.svg)

**job_ftch** is an open-source async pipeline that ingests vacancies from Telegram channels, groups, post comments, and company career sites. It normalizes them to a unified Job schema and outputs structured JSON.

## Why
AI role vacancies (LLM Engineer, AI PM, MLOps, AgentOps, AI Infra) are scattered across dozens of Telegram chats and niche career boards. job_ftch provides one async pipeline with typed stages, dedup, extraction, review routing, rejected-item feedback, and source-level isolation.

## Quick Start
1. Clone and install dependencies.

```bash
git clone https://github.com/[owner]/job_ftch
cd job_ftch
uv sync
```

2. Create a local config file.

```bash
cp .env.example .env
```

3. Run the fixture-backed pipeline.

```bash
uv run python app.py \
  --source-path fixtures/e2e/multisource_positive.jsonl \
  --output-path artifacts/debug/jobs.json \
  --review-output-path artifacts/debug/review.jsonl \
  --rejected-output-path artifacts/debug/rejected.jsonl \
  --max-items 20
```

4. Inspect the outputs.
- Main jobs: `artifacts/debug/jobs.json`
- Review queue: `artifacts/debug/review.jsonl`
- Rejected items: `artifacts/debug/rejected.jsonl`
- Quarantine: `artifacts/debug/quarantine.jsonl`

## Common Runs
Run a Telegram channel once:

```bash
uv run python app.py --source-backend telegram_channel --telegram-entity ai_jobs --max-items 100
```

Run a career site once:

```bash
uv run python app.py --source-backend career_site --career-site-url https://job-boards.greenhouse.io/clickhouse
```

Evaluate extraction quality offline:

```bash
uv run python scripts/evaluate_extraction.py --fixture fixtures/extraction/gold_samples.jsonl --llm-backend heuristic
```

## Output Model
- Main output is a `Job` payload with schema version envelope in JSON mode.
- Review and rejected flows are JSONL for operator-friendly append/replay.
- Representative sample outputs live in `fixtures/examples/`.

## Documentation
- Architecture: [docs/architecture.md](docs/architecture.md)
- Vision: [docs/vision.md](docs/vision.md)
- Rules: [docs/rules.md](docs/rules.md)
- Configuration: [docs/configuration.md](docs/configuration.md)
- Source setup: [docs/source_setup.md](docs/source_setup.md)
- Examples: [docs/examples.md](docs/examples.md)
- Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md)
- Release checklist: [docs/release_checklist.md](docs/release_checklist.md)

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License
MIT
