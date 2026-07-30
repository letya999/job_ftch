---
title: "Secondary Composition Root placement in application/tenant_runner.py"
description: "**Status:** DEPRECATED"
updated: 2026-07-24
---
# Secondary Composition Root placement in application/tenant_runner.py

**Status:** DEPRECATED

Superseded by ADR-069 after removal of the custom Prometheus exporter.

## Context

In hexagonal architecture, composition roots wire all dependencies. While `application/builder.py` is the primary composition root (per ADR-039), `application/tenant_runner.py` also required infrastructure wiring for the former custom metrics adapter.

## Decision

`application/tenant_runner.py` is designated as a secondary composition root alongside `builder.py`. It is explicitly exempted from the "application/ must not import infrastructure" CI gate rule. No other file in `application/` may import from `infrastructure/`.

The CI gate command is updated to:
`grep -r "from job_ftch.infrastructure" job_ftch/application/ --include="*.py" | grep -v "builder.py" | grep -v "tenant_runner.py"`
