---
title: "106 — Optional PostgreSQL LLM dispatch"
description: "Provider-neutral durable LLM requests, Procrastinate execution, shared admission and optional home egress."
updated: 2026-11-08
---
# 106 — Optional PostgreSQL LLM dispatch

**Status**: ACCEPTED
**Date**: 2026-11-08

## Context

ETL, preflight and CAPTCHA OCR can use the same upstream account through
different named profiles. Per-client concurrency and immediate retries cannot
enforce an account-wide rate. Queueing, CLIProxy and home egress must remain
independent optional capabilities; inline OpenAI-compatible access stays valid.

## Decision

- Add an optional Procrastinate/psycopg extra, isolated from core imports.
  PostgreSQL holds request envelopes, results, durable admission and dispatch
  intent. A Procrastinate task contains only a request ID. Broker and request
  writes use one psycopg transaction in the queue repository; existing asyncpg
  stores are not changed or passed as psycopg transactions.
- Queue at the HTTP transport boundary so Instructor schemas and all existing
  LLM methods keep one implementation. The worker resolves credentials and
  upstream URLs from trusted profiles, never from queued Authorization headers.
- Request identity includes tenant, content/schema/prompt request fingerprint,
  profile revision and logical session. Store successful responses for replay.
  Queued execution has a separate bounded wait deadline. Pending calls raise
  an application-level deferred signal, never a heuristic success. Existing
  content-versioned source replay resumes processing using saved LLM results;
  cursors and processed claims must not commit pending observations.
- Serialize each quota scope across workers, reserve start times atomically,
  persist cooldown and hourly request/token budgets, and never sleep through
  long upstream waits inside a worker. Account identities and sessions are
  references, not secrets. Different tenants share quota but not results.
- The queue owns HTTP retries. Authentication/model/request errors are terminal;
  quota and transport errors use bounded persisted waits. Unknown outcomes are
  retained for operator review. Stalled-job recovery must not blindly replay
  an upstream request whose result is unknown.
- Optional home egress uses a dedicated Tailscale sidecar and Android exit node
  on home Wi-Fi. Required egress fails closed. No automatic fallback to the VPS
  address, account or model. Routing credentials are pinned by isolated CLIProxy
  instances when the installed version cannot enforce strict credential choice.
- Keep the current ingest queue and publication outbox independent. Feature
  shutdown drains or explicitly cancels requests; it never replays pending calls
  inline. Keep provider outages separate from process liveness.

## Consequences

Default profiles and core-only installs have no queue, psycopg or phone
requirement. A queued profile requires PostgreSQL and a worker. Duplicate task
delivery is safe locally, but an upstream completion lost during a process crash
cannot provide exactly-once inference. Requests/results have bounded retention;
credentials and sensitive payloads never enter operational logs.
