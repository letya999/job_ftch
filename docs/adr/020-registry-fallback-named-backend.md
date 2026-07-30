---
title: "020 — Registry Fallback via Named Backend, Not Concrete Import"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 020 — Registry Fallback via Named Backend, Not Concrete Import

**Status**: ACCEPTED
**Date**: 2026-06-08

## Context

`application/registry.py::create_store_with_fallback` contained a direct runtime import:

```python
from infrastructure.stores.in_memory import InMemoryStore
```

This violates the hexagonal architecture rule that `application/` must not import
`infrastructure/`. The violation was originally introduced for convenience: when the primary
store fails its health check, fall back to `InMemoryStore` to keep the pipeline runnable.

Research across dlt, dbt-core, Meltano, Prefect, and Apache Beam Python SDK confirms a
consistent pattern: none of them special-case a fallback via direct import in the application
core. Safe defaults are resolved as named backends through the same registry used for
everything else. (dlt uses `duckdb` as a named default destination; Prefect resolves the
default result store through a config chain of named blocks.)

## Decision

Resolve the fallback store through the existing `_store_factories` registry by the constant
name `"in_memory"`, not via a concrete import:

```python
_FALLBACK_STORE_BACKEND = "memory"

def _create_fallback_store(settings: Settings) -> object:
    factory = _store_factories.get(_FALLBACK_STORE_BACKEND)
    if factory is None:
        msg = f"Fallback store '{_FALLBACK_STORE_BACKEND}' not registered. Ensure load_extensions() ran."
        raise RuntimeError(msg)
    return factory(settings)
```

`load_extensions()` is always called before `_create_fallback_store`, so the `"in_memory"`
factory is guaranteed to be present unless someone deliberately removes it.

## Consequences

- (+) `application/` no longer imports any `infrastructure/` module — boundary clean.
- (+) The fallback is resolved through exactly the same path as any other store backend,
  consistent with how dlt/Prefect handle named defaults.
- (+) If someone renames or removes `"in_memory"`, they get a `RuntimeError` at runtime
  rather than a silent wrong-class instantiation.
- (-) Adds one indirection: the fallback class is not statically visible in `application/`.
  This is acceptable — the fallback is an operational safety net, not a domain concept.
- (=) Behavior is identical: `InMemoryStore` is still what gets returned; the path to it
  changed, not the result.
