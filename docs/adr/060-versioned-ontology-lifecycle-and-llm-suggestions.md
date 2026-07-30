---
title: "060 — Versioned ontology lifecycle and LLM suggestion approval"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 060 — Versioned ontology lifecycle and LLM suggestion approval

**Status**: ACCEPTED
**Date**: 2026-07-10
**Updated**: 2026-07-26
**Supersedes/Transitions**: ADR-030 remains the live-store implementation
record. A run consumes an immutable snapshot rather than live mutable state.

## Decision

Ontology contents are canonicalized into an immutable tenant/profile snapshot
with a deterministic version hash before a run begins. Processing uses only
that snapshot.

As of 2026-07-26, the source of truth is the compiled ontology contract in
[`docs/ontology/compiler.md`](../ontology/compiler.md). Labeled shots produce
evidence-backed compiled terms and relations; legacy roles, skills, seniority,
anti patterns, positive keywords, and negative keywords are projections from
those compiled terms.

LLM-derived terms and aliases must carry source/evidence/confidence. Accepted
terms require evidence IDs, and positive/negative overlap is invalid. Python
code may normalize, deduplicate, validate, store, and project; it must not
encode domain-specific relevance vocabularies.
