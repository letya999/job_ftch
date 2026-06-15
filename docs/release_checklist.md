# Release Checklist

## Code quality
1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run mypy .`
4. `uv run pytest tests/`
5. `uv run bandit -r job_ftch scripts/check_module_boundaries.py -ll`

## Pipeline verification
1. Run the local fixture flow from `README.md`.
2. Verify `artifacts/debug/jobs.json` is produced.
3. Verify review, rejected, and quarantine outputs are produced when expected.
4. Run `uv run python scripts/evaluate_extraction.py --fixture fixtures/extraction/gold_samples.jsonl --llm-backend heuristic`.
5. Confirm run-state markers are written by the active store backend.

## Source verification
1. Test at least one Telegram source with real credentials.
2. Test at least one allowed career-site URL.
3. Confirm source retry and timeout settings are present in the release env file.

## Documentation
1. `.env.dev.example`, `adapters/telegram_bot/.env.dev.example`, and `adapters/telegram_bot/.env.prod.example` are current.
2. `README.md` runnable flow matches the shipped CLI.
3. `docs/source_setup.md` and `docs/troubleshooting.md` cover operator basics.

## Release contour
1. Branch is green on local gates.
2. PR targets `dev`.
3. Release notes mention source coverage, extraction backend used, and known limitations.
