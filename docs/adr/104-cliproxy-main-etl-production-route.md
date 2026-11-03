---
title: "104 — CLIProxy as the production ETL provider"
description: "Explicit node bindings allow a controlled CLIProxy route for the production bot when direct OpenAI quota blocks ingestion."
updated: 2026-11-03
---
# 104 — CLIProxy as the production ETL provider

**Status**: ACCEPTED
**Date**: 2026-11-03
**Extends**: [100 — Node-scoped LLM providers](100-node-scoped-llm-provider-routing.md)

## Context

The `ai_jobs` scheduler cannot ingest while its required direct OpenAI bindings
fail quota preflight. CLIProxy already runs beside the bot and has a separately
authenticated Google session. ADR 100 initially restricted that provider to
image CAPTCHA; this decision authorizes a controlled production ETL route.

## Decision

- Bind extraction, relevance, ontology, and presentation explicitly to a
  `cliproxy_main` profile in the Telegram production overlay. Keep the shared
  runtime and frozen OpenAI champion recipe unchanged.
- Use the existing CLIProxy client credential reference and the Compose-internal
  endpoint. Never copy OAuth tokens or client keys into YAML or the image.
- Pin a model verified for ordinary chat and the structured extraction and
  relevance schemas used by the production adapter. Select Instructor JSON
  mode for the CLIProxy main profile because real relevance responses can
  arrive as JSON text without tool calls. Keep the frozen OpenAI
  recipe as a benchmark; require an isolated comparative eval before promoting
  this model as a new champion.
- Keep CAPTCHA on its independent binding. Do not enable the legacy global
  `JOB_FTCH_LLM_GATEWAY` switch or silently fall back to another model.
- Release by rebuilding the existing bot image, then verify provider preflight,
  scheduled ingest, and confirmed Telegram delivery. Retain the previous
  overlay and image for rollback. Do not reset or replay production state.

## Consequences

The live bot may use a different model than the historical OpenAI champion.
Provider/model identity and evaluation evidence must therefore accompany the
release. CLIProxy outage or account quota blocks dependent runs without
changing process liveness; the existing durable scheduler and delivery journal
remain responsible for continuation.
