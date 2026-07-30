---
title: "Adapters and plugins"
description: "`job_ftch` uses several extension shapes. The names are intentionally"
updated: 2026-07-24
---
# Adapters and plugins

`job_ftch` uses several extension shapes. The names are intentionally
separate because they describe different responsibilities.

## Port

A port is a stable application contract. Examples:

- `Source[T]`
- `Stage[In, Out]`
- `Sink[T]`
- `Store`
- `AuthProvider`
- `LLMProvider`

Ports live in `application/` and must not depend on concrete infrastructure.

## Port adapter

A port adapter implements one port for a concrete external system or backend.

Examples:

- Telegram, RSS, API, and career-site `Source` implementations
- SQLite/PostgreSQL `Store` implementations
- JSON/Telegram posting `Sink` implementations
- OpenAI/heuristic `LLMProvider` implementations

Port adapters live in `infrastructure/`, `sinks/`, or another implementation
package. They normalize external behavior into the port contract.

## Runtime adapter

A runtime adapter is an external entry point to the product runtime. It
usually orchestrates multiple application services rather than implementing
one core port.

Examples:

- Telegram bot
- MCP server
- FastAPI bridge
- FastStream worker
- Dagster wrapper

Runtime adapters should call public application/runtime APIs. They should not
own source parsing, source assessment policy, matching, or posting decisions.

## Assessment adapter

An assessment adapter evaluates a `SourceSpec` before ingest. It does not
fetch `RawItem` values and does not run pipeline nodes.

It returns:

- source capabilities
- evidence for those capabilities
- assigned ingest strategy
- bootstrap plan

Assessment adapters are used by `SourceAssessmentService` during runtime
source onboarding and lazy base-source assessment.

## Plugin

A plugin is a discovery and registration mechanism. It is not a separate
architectural role.

Any implementation can be connected plugin-style through a registry: source
adapters, sinks, stores, monitors, scrapers, site parsers, or assessment
adapters. The plugin mechanism must not blur what responsibility the
registered object has.
