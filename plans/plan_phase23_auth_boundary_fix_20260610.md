# Fix plan — Phase 23 module-boundary violation (application/auth.py)

## Context
Work in worktree `C:/Users/User/a_projects/job_ftch_p2325` on branch `feat/phases-23-25` (cd into it first; run all commands there).

Phases 23-25 are implemented and committed (6282f42, 3b788af, 2c00554). All gates pass EXCEPT the project's own module-boundary checker:

```
uv run python scripts/check_module_boundaries.py
job_ftch/application/auth.py:7 disallowed application import: job_ftch.infrastructure.auth.env_auth
job_ftch/application/auth.py:8 disallowed application import: job_ftch.infrastructure.auth.file_auth
```

`application/` may import ONLY domain + stdlib + pydantic (+ structlog/yaml/opentelemetry). Only `builder.py` and `pipeline.py` are exempted (see `scripts/check_module_boundaries.py` lines 68-71). The new `application/auth.py` is NOT exempt and hard-imports concrete infra providers. Its `if/elif` name dispatch ALSO violates AGENTS.md "New adapter backends must self-register. No if/elif dispatch by adapter kind in core."

## Required fix — use the existing registry self-registration pattern (mirror `register_bypass`/`register_store`)

### 1. `job_ftch/application/registry.py`
- Add a `register_auth_provider(name: str)` decorator, mirroring `register_bypass` (around line 162). It registers a factory callable into a new module-level dict `_AUTH_PROVIDERS: dict[str, Callable[..., object]]`.
- Add factory `create_auth_provider(name: str | None, settings: Settings) -> object`:
  - `load_extensions()` first.
  - normalize: `normalized = (name or "env").strip().lower()`.
  - look up `_AUTH_PROVIDERS[normalized]`; if missing → `ValueError(f"Unsupported auth provider: {name}")`.
  - call the registered factory with `settings` and return the instance.
- Add the three auth modules to the `load_extensions()` builtins tuple (after the llm entries, ~line 300):
  `"job_ftch.infrastructure.auth.env_auth"`, `"job_ftch.infrastructure.auth.file_auth"`, `"job_ftch.infrastructure.auth.vault_auth"`.

### 2. Self-register in infra auth modules
Each registers a factory `(settings) -> AuthProvider`. Keep the provider classes unchanged; add a registered factory function at module bottom. Registry import is allowed in infrastructure/.
- `job_ftch/infrastructure/auth/env_auth.py`:
  ```python
  from job_ftch.application.registry import register_auth_provider

  @register_auth_provider("env")
  def _create_env_auth(settings: "Settings") -> "EnvAuthProvider":
      return EnvAuthProvider()
  ```
- `job_ftch/infrastructure/auth/file_auth.py`:
  ```python
  @register_auth_provider("file")
  def _create_file_auth(settings: "Settings") -> "FileAuthProvider":
      if settings.auth_file_path is None:
          raise ValueError("auth_file_path is required when auth_provider=file.")
      return FileAuthProvider(settings.auth_file_path)
  ```
- `job_ftch/infrastructure/auth/vault_auth.py`:
  ```python
  @register_auth_provider("vault")
  def _create_vault_auth(settings: "Settings") -> "VaultAuthProvider":
      raise NotImplementedError("VaultAuthProvider is not implemented in this project.")
  ```
  (If `VaultAuthProvider` does not exist or differs, register a factory that raises `NotImplementedError` to preserve current behavior. Match the actual class names in each file.)
- Use `from __future__ import annotations` + `TYPE_CHECKING` for the `Settings` hint to avoid import cycles; do NOT import `Settings` at runtime inside infra if it creates a cycle (string annotation is enough).

### 3. Replace `job_ftch/application/auth.py`
Remove the infra imports and the if/elif. Make it a thin delegator so `tenant_runner.py` keeps working unchanged:
```python
"""Auth provider resolution for runtime adapters and tenant runners."""
from __future__ import annotations
from typing import TYPE_CHECKING
from job_ftch.application.registry import create_auth_provider
if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.config import Settings

def resolve_auth_provider(provider_name: str | None, *, settings: Settings) -> AuthProvider:
    return create_auth_provider(provider_name, settings)  # type: ignore[return-value]
```
`tenant_runner.py` import (`from job_ftch.application.auth import resolve_auth_provider`) stays as-is.

## Verify (all must pass, in the worktree)
1. `uv run python scripts/check_module_boundaries.py` → NO violations (empty/exit 0).
2. `uv run ruff check . && uv run ruff format --check .` → clean.
3. `uv run mypy .` → clean.
4. `uv run pytest tests/ -q` → 242+ passed, 0 failed (the lone aiosqlite teardown warning is acceptable).
5. Confirm an auth-provider resolution still works (e.g. existing phase-23 tests using auth=env still pass; add an assertion in `tests/test_phase23_tenants.py` if none covers `resolve_auth_provider("env", settings)` and `create_auth_provider`).

## Commit
Amend into the phase-23 commit is NOT required; add a follow-up fixup commit on `feat/phases-23-25`:
`fix(tenant): register auth providers via registry to satisfy module boundaries`
Keep the working tree clean afterward. Do NOT merge to парсеры/main.
