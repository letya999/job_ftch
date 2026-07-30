---
title: "ADR-045: Managed shot backend boundary."
description: "Status: ACCEPTED"
updated: 2026-07-24
---
# ADR-045: Managed shot backend boundary.

Status: ACCEPTED
Date: 2026-06-25.

## Context.

`application/shot_sync.py` is an important cross-runtime capability: it keeps
user-managed positive/negative examples ("shots") consistent between candidate
profiles, the in-process scorer-visible store, and the persistent backend used
across restarts.

The capability is not Telegram-specific. It is used by the bot today, but the
same behavior is needed by any future CLI, MCP, API, or background workflow
that edits managed candidate profiles and expects the relevance scorer to see
the same shots.

However, the existing implementation lived in `application/shot_sync.py` while
directly importing concrete infrastructure concerns:

- `BgeMThreeProvider`
- `BgeMThreeQdrantShotStore`
- `shot_registry`
- runtime `Settings()`

This violated the project rule that `application/` must not import
`infrastructure/` outside the composition root exceptions. It also mixed three
responsibilities in one module:

1. application use-case orchestration,
2. scorer/persistence synchronization mechanics,
3. backend-specific embedding and storage details.

## Decision.

`shot_sync` remains an application capability, but concrete shot synchronization
mechanics move behind a dedicated application port.

We introduce a single use-case-shaped port for managed shots. The port owns the
backend operations required by the capability:

- add a single shot,
- remove a single shot,
- remove all shots for a user,
- rebuild scorer-visible shots from a managed profile.

`application/shot_sync.py` stays in `application/`, but becomes orchestration
only. It may call domain/application helpers and the managed-shot port, but it
must not import concrete infrastructure backends directly.

Concrete implementations live in infrastructure:

- an in-memory implementation for tests and lightweight runtime wiring,
- a BGE-M3 + Qdrant-backed implementation that preserves current production
  behavior,
- an infrastructure registry bridge implementation may compose the in-process
  `shot_registry` mirror with the persistent backend so the scorer-visible
  runtime state and persistent state stay aligned.

Composition root code is responsible for choosing and wiring the concrete
backend.

## Consequences.

The capability remains reusable outside Telegram while restoring the module
boundary contract.

`application/shot_sync.py` becomes a stable use-case API rather than a concrete
runtime implementation detail. The scorer-visible in-memory mirror and the
persistent shot backend remain supported, but the knowledge of how they are
wired moves out of `application/`.

This adds one new port and concrete backend wiring, but it prevents further
spread of Qdrant/BGE/settings knowledge through the application layer and makes
future non-Telegram managed-shot entry points straightforward.
