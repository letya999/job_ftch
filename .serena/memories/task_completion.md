# Task Completion

- Standard verification gates from `docs/rules.md` and `CONTRIBUTING.md`:
  `uv run ruff check .`
  `uv run ruff format --check .`
  `uv run mypy .`
  `uv run pytest tests/`
  `uv run bandit -r . -ll`
- If dependencies or architecture changed:
  update `docs/tech_stack.md` for dependency rationale.
  add/update ADR under `docs/adr/` for nontrivial design decisions.
- If task touches pipeline ordering, verify `SanitizeNode` remains first.
- If task adds real behavior, expand tests beyond current smoke coverage.
