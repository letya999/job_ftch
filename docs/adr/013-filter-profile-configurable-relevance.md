---
title: "013 — FilterProfile: configurable relevance"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 013 — FilterProfile: configurable relevance

**Status**: ACCEPTED
**Date**: 2026-06-07

## Context

Phase 13 addresses a hardcoded `_POSITIVE_KEYWORDS` list inside `TriageNode`. This is a source of friction: different operators have different job role definitions, and every change requires a code edit and redeploy. Industry-standard filtering services expose relevance as configuration, not code.

## Decision

Introduce `FilterProfile` as a first-class domain object in `domain/filter_profile.py`:

```python
class FilterProfile(BaseModel):
    positive_keywords: list[str]
    negative_keywords: list[str]
    required_patterns: list[str]  # all must match
    min_score: float = 0.0  # item score threshold after scoring stage
    case_sensitive: bool = False
```

`FilterProfile` is loaded from YAML (per-tenant or default) and injected into `TriageNode` at build time via `PipelineBuilder`. Multiple profiles can coexist; operators switch profiles by changing config, not code.

Built-in profiles ship in `config/profiles/`: `ai_roles.yaml`, `ai_jobs_ru_kz.yaml`, `all_roles.yaml` (no-op passthrough). Operators can provide custom profiles without modifying the package.

## Consequences

- (+) Relevance tuning is an operator concern, not a developer concern.
- (+) Multiple tenants with different role filters share the same codebase.
- (+) Profiles are testable as pure data (no pipeline setup needed).
- (-) Operators must understand keyword semantics; poorly written profiles cause false drops.
