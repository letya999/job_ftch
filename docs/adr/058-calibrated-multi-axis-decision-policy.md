---
title: "058 — Calibrated multi-axis decision policy and single DecisionNode"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 058 — Calibrated multi-axis decision policy and single DecisionNode

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-041, ADR-042 and ADR-044 remain historical
scoring descriptions. Terminal lane selection is owned by this policy.

## Decision

Scorers emit artifacts; a pure decision table combines relevance, hard
constraints, freshness, risk, quality, and degradation. Confirmed hard
constraints, closed/expired lifecycle, and high posting risk veto ACCEPT.
Unknown/degraded evidence routes to REVIEW. Quality describes evidence
completeness and does not silently redefine relevance.
