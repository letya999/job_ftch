---
title: "007 - Extension Registry And Plugin Discovery"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 007 - Extension Registry And Plugin Discovery

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
`app.py` and career-site parsing previously used hardcoded `if/elif` dispatch. That forces core edits for every new source, sink, store, or parser and makes third-party extensions impossible without forking.

## Decision
Adopt a two-tier extension system:
- builtin registries in `application/registry.py` with `@register_source`, `@register_sink`, `@register_store`, and `@register_parser`;
- optional third-party discovery via Python entry points (`job_ftch.sources`, `.parsers`, `.sinks`, `.stores`).

Builtin modules are imported once to trigger self-registration; external packages can register on load through entry points.

## Consequences
- (+) Adding an adapter no longer requires changing core dispatch code.
- (+) Community packages can extend the pipeline without patching the repo.
- (-) Registration now depends on import-time side effects, so missing imports must be covered by tests.
