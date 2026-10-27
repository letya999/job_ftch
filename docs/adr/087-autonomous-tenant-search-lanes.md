---
title: "087 — Autonomous tenant search lanes"
description: "Tenant is the unit of isolated scheduling, configuration, execution and storage."
updated: 2026-09-06
---
# 087 — Autonomous tenant search lanes

**Status**: ACCEPTED  
**Date**: 2026-09-06

## Context

Jobfetch must run independently of any downstream product and preserve its high-recall ingest role. Different job-search directions need different sources, profiles, ontologies, pipeline graphs, models and schedules. Treating those directions as profiles inside one runtime couples their execution and makes metrics ambiguous.

The current tenant runner already isolates settings, stores, sources and runtime backends, while the production `ai_jobs` pipeline must continue unchanged.

## Decision

1. A tenant is an independent search lane and the unit of scheduling, execution, storage, metrics and API authorization scope.
2. Tenant configuration references its own profile, ontology artifacts, sources and pipeline recipe. A profile remains data used by a tenant; it is not a runtime scope.
3. Pipeline behavior is entirely defined by the tenant's graph recipe. Relevance prefiltering remains an ordinary optional node with normal node configuration.
4. The scheduler starts due tenants without requiring Telegram publishing configuration. Overlapping runs of the same tenant are skipped; different tenants may run concurrently within existing limits.
5. Every run records the resolved configuration identity, trigger, start/end time, status and one outcome for every configured source, including sources that were disabled, not due, rate-limited or blocked by a budget.
6. Raw observations are stored before relevance decisions. Accepted, review and rejected outcomes remain queryable according to tenant retention settings. No successful observation may exist only in logs or webhook delivery.
7. The existing `ai_jobs` tenant and its recipe retain their current defaults and behavior. New tenant capabilities are opt-in configuration.

## Consequences

- (+) Wide engineering, technical project management and AI product/project management searches can be separate tenants without a new profile-scope abstraction.
- (+) Each search lane can be run, disabled, scheduled, replayed and measured independently.
- (+) Jobfetch remains useful without CareerGo or any other consumer.
- (-) Shared profile material is duplicated or referenced by configuration until repeated maintenance proves a shared-profile abstraction necessary.
- (-) Retaining all outcomes requires an explicit storage policy and monitoring of database growth.
