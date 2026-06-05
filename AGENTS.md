# Project Overview
job_ftch is an open-source async pipeline that ingests vacancies from Telegram channels, groups, comments and career sites, normalizes them, deduplicates, optionally AI-screens, and emits structured JSON.

# Core Principles
1. Simplicity over cleverness — if it can be done in 10 lines, do not use a framework
2. Extensibility over completeness — add extension points, not features prematurely
3. Open source cleanliness — no vendor lock-in, no proprietary SDKs as hard deps
4. Light DDD — use DDD vocabulary (Entity, Value Object, Repository, Bounded Context) without ceremony (no event sourcing, no CQRS in v0)
5. No overloading — each file does one thing; each function has one responsibility
6. Explicit > implicit — no magic, no DI containers, composition in app.py

# Architecture Summary
- 5 Protocols: Source, Node, Sink, Store, LLMProvider
- Domain core: pure Pydantic models, zero I/O imports
- Infrastructure adapters: implement Protocols
- Pipeline engine: ~25 lines, composes everything

# Working with this repo as an AI agent
- Always read docs/architecture.md before writing code
- Always read docs/tech_stack.md before choosing libraries
- Never add a new library without updating docs/tech_stack.md
- ADR for every architectural decision in docs/adr/
- Prefer editing existing files over creating new ones
- Write tests before/alongside implementation (TDD)
- Run `uv run ruff check .` before finishing
- Domain layer: zero imports outside pydantic and stdlib
- Never put I/O in domain/
- Infrastructure adapters: one file per adapter
- If uncertain about a choice, write an ADR and mark it PROPOSED

# Extension points
- New source: implement Source Protocol in infrastructure/sources/
- New processing step: implement Node Protocol in nodes/
- New output: implement Sink Protocol in sinks/
- New storage backend: implement Store Protocol in infrastructure/stores/
- New LLM backend: implement LLMProvider Protocol in infrastructure/llm/

# Security rules
- SanitizeNode MUST be first in every pipeline chain
- Never pass raw scraped text directly to LLM without sanitization
- No credentials in code — only via env vars
- Validate all URLs before HTTP requests (no SSRF)

# Commit rules
feat, fix, chore, docs, refactor only. No "WIP", no "update", no "fix stuff"

# DO NOT
Add Kafka, Celery, Airflow, LangChain, LangGraph, heavy ORMs, Scrapy as core deps
