# 001 — Hexagonal Architecture

**Status**: ACCEPTED
**Date**: 2026-06-05

## Context
Need an architecture that supports pluggable sources, processors, and sinks while keeping domain logic pure and infrastructure swappable.

## Decision
Hexagonal Architecture (Ports & Adapters) with 5 core Protocols: Source, Node, Sink, Store, LLMProvider.

## Consequences
- (+) Zero coupling between domain and infra.
- (+) Trivial testing (InMemoryStore).
- (+) Easy K8s/scaling later.
- (-) Explicit composition in app.py (acceptable for this size).
