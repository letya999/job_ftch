---
title: "059 — Provisional identity index and canonical JobGroup commit"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 059 — Provisional identity index and canonical JobGroup commit

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-016's early aggregation order is superseded.

## Decision

Identity candidates may be computed before policy, but canonical `JobGroup`
mutation occurs only after terminal policy accepts or reviews the record.
Rejected records cannot create or merge canonical groups.
