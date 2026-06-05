# Suggested Commands

- Install/sync env: `uv sync`
- Run entrypoint: `uv run python app.py`
- Run tests: `uv run pytest tests/`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`
- Type check: `uv run mypy .`
- Security scan: `uv run bandit -r . -ll`
- Windows shell equivalents worth remembering:
  list files: `Get-ChildItem`
  read file: `Get-Content <path>`
  repo file list: `rg --files`
  exact search: `rg "pattern" -g '*.py'`
- Git flow in docs:
  branch from `dev`.
  PRs target `dev`, not `main`.
