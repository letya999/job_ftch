# Phase 4 Completion

Completed on 2026-06-06.

Scope implemented:
- `RM-022` raw-item identity with processed keys derived from `source_kind + source_name + external_id/url`.
- `RM-023` exact dedup by canonical URL and normalized content signals.
- `RM-024` near-duplicate detection using `rapidfuzz`.
- `RM-025` cross-source dedup between Telegram and career-site `RawItem` payloads.
- `RM-026` duplicate explainability persisted via store-backed duplicate records.

Design decisions captured:
- See ADR `docs/adr/005-raw-item-identity-and-dedup.md`.
- Dedup happens on `RawItem` before extraction exists, via `nodes.DedupNode`.
- `Pipeline` marks processed keys for terminal outcomes, not just successful emits, to keep reruns idempotent.

Verification completed with repo gates:
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy .`
- `uv run pytest tests/`
- `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll`
