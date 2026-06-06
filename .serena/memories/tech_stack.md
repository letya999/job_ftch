# Tech Stack

- Language/runtime: Python 3.12+, asyncio.
- Package/env manager: `uv`.
- Build backend: `hatchling`.
- Config: `pydantic-settings`, `.env` via `config.py`.
- Core deps already declared in `pyproject.toml`:
  `pydantic`, `pydantic-settings`, `httpx`, `selectolax`, `telethon`, `rapidfuzz`, `openai`, `instructor`, `opentelemetry-api`, `opentelemetry-sdk`, `structlog`.
- Dev tooling:
  `ruff` for lint + format.
  `mypy` in strict mode.
  `pytest`, `pytest-asyncio`, `pytest-cov`.
  `bandit`.
- Packaging layout is flat package directories, not `src/`.
- Wheel packages currently include `domain`, `application`, `infrastructure`, `nodes`, `sinks`.
