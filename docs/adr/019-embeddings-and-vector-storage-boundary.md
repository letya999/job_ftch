---
title: "ADR 019: Embeddings and Vector Storage Boundary"
description: "ACCEPTED"
updated: 2026-07-24
---
# ADR 019: Embeddings and Vector Storage Boundary

## Status
ACCEPTED

## Context
With the introduction of semantic search, we need to generate and store high-dimensional vectors (embeddings) for job listings. However, coupling these vectors tightly to the domain entities would bloat the `Job` model and make it dependent on specific ML/infrastructure details.

## Decision
* `Job` domain models will never store embedding vectors directly.
* Embeddings are produced as an optional pipeline stage *after* validation, normalization, scoring, and grouping.
* Vectors are strictly stored through an isolated `VectorBackend`.
* Vector payloads will contain lookup metadata only (e.g., IDs, critical facets) rather than full domain representations.
* Hybrid search will merge ranked group IDs from full-text and vector backends, not domain objects directly.

## Consequences
* We maintain a clean architecture where domain logic is free from vector/ML concerns.
* Switching vector databases (e.g., from Qdrant to pgvector) requires zero changes to the core `Job` and `JobGroup` structures.
* We need a stable mechanism for reciprocal rank fusion (RRF) using primitive IDs before hydrating the final domain objects for the user.
