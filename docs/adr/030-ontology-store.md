---
title: "020 — Ontology Storage Strategy: DB-backed with File Fallback"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 020 — Ontology Storage Strategy: DB-backed with File Fallback

**Status**: ACCEPTED
**Date**: 2026-06-17

## Context

The skill/role/seniority/anti-pattern ontology needs to be:
- Read on every extraction (high frequency, low latency required)
- Updated live when shots are uploaded (low frequency, async)
- Available across runs and across profiles
- Survive `Store` reset (dedup-only reset must not wipe ontology)

## Decision

**`OntologyStore` Protocol** in `application/contracts.py`. Two implementations selected by `store_backend`:

### DBOntologyStore (default when store is DB)
- Used when `settings.store_backend ∈ {"sqlite", "postgresql"}`
- Stores in same DB as dedup (e.g. `jf_ontology_skill`, `jf_ontology_role`, `jf_ontology_seniority`, `jf_ontology_anti`)
- Uses existing `SQLStoreAdapter` — no new DB connection
- Migrations added to `sql_migrations.py`
- Survives `Store` reset because tables are separate

### FileOntologyStore (fallback for dev without store)
- Used when `store_backend ∉ {"sqlite", "postgresql"}` (memory mode or no store)
- Stores as JSON in `infrastructure/ontology/data/ontology_*.json`
- Format: `{"python": {"canonical": "python", "aliases": {"en": ["py", "python3"], "ru": ["питон"]}, "updated_at": "..."}}`
- Async file I/O via existing `aiofiles`

### Selection in `builder.py`

```python
def build_ontology_store(settings) -> OntologyStore:
    if settings.store_backend in {"sqlite", "postgresql"}:
        return DBOntologyStore(settings)
    return FileOntologyStore(settings)
```

### Read caching

`OntologyNormalizer` keeps an in-process snapshot:
- Loaded on init from `OntologyStore`
- Refreshed via `await refresh()` after each ontology update
- Sync reads in hot path use the snapshot, not the store

### Self-registration

Both implementations use `@register_ontology_store("db")` and `@register_ontology_store("file")` decorators. Lookup is by string name in the registry.

## Consequences

- (+) Same DB for everything. No new infrastructure.
- (+) Ontology survives dedup reset.
- (+) File fallback works for dev/test without any DB.
- (+) In-process snapshot keeps hot path fast.
- (-) Two implementations to maintain. Acceptable (one is trivial file I/O).
- (-) File fallback is not concurrent-safe across processes. Dev-only.
