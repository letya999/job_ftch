---
title: "110 — CLIProxy operator recovery"
description: "Atomic checkpoint handoff and immediate owner notice after an unknown CLIProxy outcome."
updated: 2026-11-14
---
# 110 — CLIProxy operator recovery

**Status**: ACCEPTED
**Date**: 2026-11-14

## Context

ADR 106/107 deliberately pause an account when an HTTP outcome is unknown. An
operator must inspect that attempt before continuing. Releasing the old run and
starting the bot before moving its observation checkpoint creates a race with
the bot's in-memory checkpoint and can overwrite pending references. Repeated
timeouts have also remained unnoticed because ordinary `waiting_llm` and an
operator hold share the same owner-notice key.

## Decision

- Provide an explicit CLIProxy recovery command for one reviewed unknown work
  request. It acquires the same tenant run lock as ingestion, validates the run,
  scope and checkpoint, and atomically cancels the unknown request, releases the
  old run, creates a new pinned run with the same route, transfers the checkpoint
  and last source-acquisition timestamp, then resumes the scope. Other active or
  unknown work rejects recovery.
- Require the command to run with the shared tenant lock directory and an
  explicit reviewed-outcome flag. It never runs automatically or claims that a
  cancelled inference did not execute at Google.
- Treat an operator hold as a distinct owner-notice transition, so the bot
  alerts the configured owner at its next scheduler pass even when the previous
  state was already `waiting_llm`.
- Increase the production text request timeout to 360 seconds after observed
  successful responses approached three minutes. Keep the short OCR timeout and
  the unknown-outcome safety behavior.

## Consequences

The operator can recover a held run without losing saved observations or racing
an active tenant run. A longer timeout reduces premature client disconnects but
cannot guarantee an upstream response; a later unknown outcome still pauses
the account for review. Inline/direct LLM profiles remain unchanged.
