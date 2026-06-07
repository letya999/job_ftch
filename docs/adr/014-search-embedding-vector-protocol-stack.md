# 014 — Search, embedding, and vector protocol stack

**Status**: ACCEPTED
**Date**: 2026-06-07

## Context

Phase 14 adds full-text and semantic search over stored jobs. A naive approach would hard-code PostgreSQL FTS or a specific vector database. The project philosophy requires: lightweight default (no external infra), protocol-driven extensibility (swap backend by config), and optional heavy deps (qdrant-client, sentence-transformers in extras groups only).

## Decision

Three independent protocol stacks, each mirroring the StoreConnector pattern (ADR-012):

### Search backend

```
SearchBackend  (protocol)
  ├─ PostgreSQLFTSBackend   (tsvector + GIN index, built-in Postgres)
  └─ PgVectorBackend        (pgvector extension; FTS + vector hybrid)
```

SQLite FTS5 via `SQLiteJobBackend` serves as the zero-infra default (no external search engine required).

### Embedding provider

```
EmbeddingProvider  (protocol)
  ├─ OpenAIEmbeddingProvider        (text-embedding-3-small / large)
  └─ SentenceTransformersProvider   (local, offline, extras [embeddings])
```

`EmbeddingProvider.embed(texts: list[str]) → list[list[float]]` — batch interface. Provider is injected, not imported directly inside nodes.

### Vector backend

```
VectorBackend  (protocol)
  ├─ QdrantVectorBackend    (qdrant-client, extras [qdrant]; optimised for high-volume)
  └─ PgVectorBackend        (pgvector; convenient if PostgreSQL already in use)
```

Hybrid search (FTS + vector) uses Reciprocal Rank Fusion (RRF) to merge ranked result lists without score calibration.

## Consequences

- (+) Full-text search works with zero additional infra (SQLite FTS5).
- (+) Vector search is entirely optional and adds qdrant-client only when needed.
- (+) EmbeddingProvider is swappable: local model for privacy, OpenAI for quality.
- (+) RRF avoids the need to calibrate score scales across FTS and vector results.
- (-) Three protocol stacks increase surface area; each backend requires independent tests.
- (-) Qdrant requires running a separate service in production.
