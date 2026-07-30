---
title: "056 — Structured evidence, jobness, and extraction"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 056 — Structured evidence, jobness, and extraction

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-024's canonical contract remains active for
downstream records. The completeness-gate fast path no longer treats parser
metadata as a vacancy decision.

## Context

Structured API, JSON-LD, and ATS fields are useful evidence, but field
completeness does not prove a page is a live vacancy. The previous fast path
turned metadata directly into `JobDraft`, set `post_type=job_posting`, and set
`hiring_intent=1.0`, conflating extraction with policy.

## Decision

Source parsers emit immutable field-level `StructuredSourceEvidence` with
field name, source span/value, page kind, parser version, provenance, and
confidence. `CompletenessGateNode` may annotate extraction cost hints but may
not create a `JobDraft`, assign post type, or assign hiring intent.

`JobnessDecision` is a separate artifact containing probability, hiring
intent, post-type distribution, evidence, and uncertainty. Extraction may
consume it but cannot overwrite it. Non-null critical extracted fields require
source evidence or explicit inferred provenance.

## Consequences

- Listing pages cannot become a detail vacancy solely from structured fields.
- Fast/full extraction disagreement is measurable from stored artifacts.
- Jobness metrics and extraction field metrics can be evaluated separately.
