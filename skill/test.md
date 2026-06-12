# job_ftch test runner

Use when the user wants to run the test suite or verify changes.

Triggers:
- `/test`
- `/pytest`

Canonical commands (quiet, context-safe):
- Targeted (fix-test loop): `uv run pytest tests/test_<name>.py -q -o addopts="" --tb=line`
- Full to file: `uv run pytest -q -o addopts="" --tb=short > .pytest.out 2>&1; tail -n 20 .pytest.out`

Why: `addopts="-v"` in `pyproject.toml` makes default `uv run pytest` verbose. Under an
AI agent, running the full verbose suite repeatedly in foreground exhausts the context
window (measured: ~1.1M tokens verbose loop vs ~13K quiet). `-o addopts=""` overrides `-v`
on the command line only, leaving verbose output intact for humans and CI.

Never run the full suite with `-v` repeatedly in foreground.
