# Task Completion

- Standard verification gates:
  `uv run ruff check .`
  `uv run ruff format --check .`
  `uv run mypy job_ftch/`
  `uv run pytest tests/`  ← MUST use `uv run`, not `python -m pytest` (system Python lacks dev deps)
  `python scripts/check_module_boundaries.py`
  `uv run bandit -r job_ftch/ -ll`

- Expected baseline: 592 passed, 11 skipped, 0 errors (as of MVP commit 7af8a54).

- If dependencies or architecture changed:
  update `docs/tech_stack.md` for dependency rationale.
  add/update ADR under `docs/adr/` for nontrivial design decisions.
- If task touches pipeline ordering, verify `SanitizeNode` remains first.
- If task adds real behavior, expand tests beyond current smoke coverage.
- If task adds optional dependency (like aiogram): guard tests with `pytest.importorskip("package_name")`.
- If task moves code from `job_ftch/adapters/` to anywhere else: check `scripts/check_module_boundaries.py`
  and update `application_runtime_exception` set if the new location is in `application/` but
  legitimately imports `infrastructure/` at runtime.
- Root `adapters/` is NOT in the wheel; Dockerfiles must set `ENV PYTHONPATH=/app`.
