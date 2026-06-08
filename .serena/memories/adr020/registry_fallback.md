# ADR-020: Registry Fallback via Named Backend

Date: 2026-06-08. Status: ACCEPTED.
Full text: `docs/adr/020-registry-fallback-named-backend.md`

## Problem

`application/registry.py::create_store_with_fallback` had a direct import:
`from infrastructure.stores.in_memory import InMemoryStore`
This violates hexagonal rule: `application/` must NOT import `infrastructure/`.

## Decision

Resolve fallback through `_store_factories` registry by constant name `"memory"`:
```python
_FALLBACK_STORE_BACKEND = "memory"

def _create_fallback_store(settings: Settings) -> object:
    factory = _store_factories.get(_FALLBACK_STORE_BACKEND)
    if factory is None:
        raise RuntimeError(...)
    return factory(settings)
```

`load_extensions()` is always called before `_create_fallback_store`, so the key
`"memory"` is guaranteed present (registered by `infrastructure.stores.in_memory`).

## Rationale

Research across dlt, dbt, Meltano, Prefect, Beam confirms: all resolve fallbacks
through named-backend registry, never via concrete import in application core.

## Verification

- Layer boundary check: `Select-String -Path application/*.py -Pattern "from infrastructure"` returns empty.
- Tests: `tests/test_registry_fallback.py` (5 tests) includes a static source scan
  asserting no `from infrastructure` lines remain in `application/registry.py`.
- All 180 tests pass. `mypy`, `ruff`, `bandit` clean.
