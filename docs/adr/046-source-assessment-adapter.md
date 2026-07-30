---
title: "046 — Source assessment adapter"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 046 — Source assessment adapter

**Status**: ACCEPTED
**Date**: 2026-06-27

## Context

Runtime users can add Telegram sources, RSS feeds, known APIs, and generic
career-site URLs from adapters such as the Telegram bot or API bridge. The
existing `Source` contract answers a different question: it fetches
`RawItem` values for the pipeline. It must not also decide whether a source
has timestamps, stable URLs, sitemap inventory, or should run a bootstrap
without LLM calls.

The word "adapter" is overloaded in the project. We need a precise taxonomy
before adding source intelligence:

- a port adapter implements one application port, such as `Source`, `Store`,
  `Sink`, or `LLMProvider`;
- a runtime adapter is an external entry point, such as the Telegram bot,
  MCP server, FastAPI bridge, or Dagster wrapper;
- an assessment adapter evaluates a source before ingest and classifies
  freshness signals;
- a plugin is a registration/discovery mechanism, not an architectural role.

## Decision

Add a pre-ingest source freshness assessment subsystem:

- domain DTOs describe capabilities, evidence, and whether freshness can be
  judged without a full snapshot;
- `SourceAssessmentAdapter` is an application-level contract;
- builtin assessment adapters cover Telegram, RSS, known integrations, and
  generic sources;
- known assessment may use registry-backed hints and bounded probes from
  already supported monitors, scrapers, and site parsers, so a plain
  career-site URL can still be classified without running normal ingest;
- `SourceAssessmentService` selects an adapter, stores the result in
  `Store.get_run_state` / `set_run_state`, and exposes the result to runtime
  listing payloads;
- assessment does not create `RawItem`, does not fetch the full source
  inventory, does not gate runtime items, and never calls LLM providers.

Assessment results are persisted under source-id scoped keys:

- `source_assessment:v2:<source_id>:report`

The existing domain-level source identity from `runtime_source.py` is used.
This keeps assessments distinct for different board URLs on the same host.

## Consequences

- (+) Runtime source onboarding can tell the caller whether freshness is
  directly knowable, indirectly knowable, or not knowable without snapshot.
- (+) Telegram/RSS/known integrations can skip generic scanning and get fixed
  strategies.
- (+) Generic sites get a conservative answer instead of a false claim about
  freshness.
- (+) Runtime adapters remain thin: they call the runner and display the
  stored assessment summary.
- (-) A new adapter category must be documented and enforced in reviews.
- (-) The first implementation is deliberately lightweight; deeper network
  scanners can be added behind the same contract later.
