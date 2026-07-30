---
title: "002 — DDD Lite"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 002 — DDD Lite

**Status**: ACCEPTED
**Date**: 2026-06-05

## Context
DDD provides useful vocabulary but full ceremony (event sourcing, CQRS) is overkill for a part-time team building a pipeline.

## Decision
DDD Lite — use Entity, Value Object, Repository (Store Protocol) patterns and Bounded Context vocabulary without event sourcing or full aggregate roots.

## Consequences
- (+) Clean domain model.
- (+) Fast iteration.
- (-) May need to evolve to fuller DDD if multi-tenant complexity grows.
