---
title: "Composition Root placement in application/builder.py"
description: "**Status:** ACCEPTED"
updated: 2026-07-24
---
# Composition Root placement in application/builder.py

**Status:** ACCEPTED

## Context

In hexagonal architecture, composition roots wire all dependencies. Pure placement would be in `adapters/`. However, `builder.py` is the sole composition root in the project and moving it requires updating 7+ importers with no behavior change.

## Decision

`application/builder.py` is designated the composition root. It is explicitly exempted from the "application/ must not import infrastructure" CI gate rule. No other file in `application/` may import from `infrastructure/`.

The CI gate command is updated to:
`grep -r "from job_ftch.infrastructure" job_ftch/application/ --include="*.py" | grep -v "builder.py"`

A module-level comment is added to `builder.py`: `# Composition root — exempt from application→infrastructure import ban per ADR-039.`
