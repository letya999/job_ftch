---
title: "049 - Library-first deployment image boundaries"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 049 - Library-first deployment image boundaries

**Status**: ACCEPTED
**Date**: 2026-07-01

## Context

`job_ftch` is a library-first project. Runtime adapters such as the Telegram bot
import the library and call its programmatic API in-process. The composition
roots are `application/builder.py` and `application/tenant_runner.py` per
ADR-039 and ADR-040.

The current Telegram bot container is convenient for the MVP, but it mixes
several responsibilities in one adapter Dockerfile:

- Telegram bot runtime dependencies.
- Library backend dependencies such as Postgres, Qdrant, OpenAI, embeddings,
  source parsers, and scraper support.
- Browser dependencies for Playwright and CloakBrowser.
- Dev and production deployment concerns.

This makes the adapter look like it owns storage, search, ML, and browser
infrastructure. It also makes browser hardening hard to reason about: adding a
browser capability currently means modifying the Telegram adapter image, even
though the capability belongs to scraping infrastructure.

At the same time, moving the pipeline behind a mandatory HTTP service would be
the wrong abstraction for this project. It would hide the existing library API
behind a network boundary only to clean up Dockerfiles.

## Decision

Keep the runtime adapter model library-first. The Telegram bot remains an
in-process adapter that imports `job_ftch` and calls the library API directly.
We do not introduce a mandatory pipeline daemon or HTTP service between the bot
and the library.

Split deployment artifacts by capability and environment:

1. **Library runtime images**

   A `job-ftch-runtime` image installs the library and the selected non-browser
   capability profile needed by a deployment: stores, job/search/vector
   backends, LLM clients, embeddings, source parsers, and scheduler runtime.
   This image is not owned by the Telegram adapter.

2. **Adapter images**

   Adapter images are thin images built on top of a runtime image. A Telegram
   bot image may install bot-only dependencies and define the Telegram entry
   point, but it must not list Postgres, Qdrant, browser, scraper, or ML
   capability dependencies directly.

3. **Browser capability stays in the runtime image**

   Playwright and CloakBrowser remain installed in the shared runtime image for
   the first migration step. They are guaranteed by the runtime image and are
   not installed from Telegram adapter Dockerfiles.

   This keeps the deployment library-first and avoids introducing an additional
   remote-browser API boundary before it is proven in production.

4. **Dev/prod separation**

   Development and production artifacts are separate files, not conditionals in
   one file. Dev may use live mounts, debug env files, model caches, and exposed
   service ports. Prod uses immutable images, production env files, internal
   ports, and explicit persistent volumes.

5. **Adapter-scoped compose**

   The Telegram deployment compose files live under `adapters/telegram_bot/`
   because they describe the Telegram adapter deployable. They may reference
   shared runtime and browser Dockerfiles, but the deployable entry point stays
   adapter-scoped.

## Target layout

```text
docker/
  runtime/
    Dockerfile.dev
    Dockerfile.prod

adapters/
  telegram_bot/
    Dockerfile.dev
    Dockerfile.prod
    docker-compose.dev.yml
    docker-compose.prod.yml
    .env.dev
    .env.dev.example
    .env.prod
    .env.prod.example
```

## Rules

- Adapter Dockerfiles define adapter entrypoints and adapter-only dependencies.
- Storage, vector, LLM, embedding, and source parser dependencies belong to
  runtime images.
- Playwright and CloakBrowser dependencies belong to the shared runtime image,
  not adapter Dockerfiles.
- Compose files wire services together; Dockerfiles define one image role.
- Dev compose must not read production env files.
- Prod compose must not read development env files.
- Root-level compose files are deprecated once adapter-scoped compose exists.

## Consequences

- The Telegram bot remains simple at the code boundary: it still calls the
  library API directly.
- Docker ownership becomes clearer: adding a new backend changes a runtime
  image; adding a new bot feature changes the bot image; adding a browser
  capability changes a browser image.
- Browser work proceeds incrementally. We first move browser installs out of
  adapter Dockerfiles into the shared runtime image. Remote browser endpoints
  remain an optional future step and are not part of this migration.
- Builds become more cacheable because adapter changes no longer invalidate
  heavy runtime or browser layers.
- The deployment tree grows, but the extra files represent real operational
  roles instead of hidden conditionals in one large Dockerfile.

## Migration plan

1. Add shared runtime Dockerfiles for dev and prod.
2. Replace Telegram bot Dockerfiles with thin images based on runtime images.
3. Move Telegram compose files into `adapters/telegram_bot/` and split dev/prod
   env inputs explicitly.
4. Keep Playwright and CloakBrowser guaranteed in the runtime image.
5. Deprecate or remove root-level compose files after the adapter-scoped compose
   is verified.

## Open questions

- Should `pyproject.toml` gain named deployment extras such as
  `runtime-postgres`, `runtime-search`, and `runtime-browser`, or should this
  remain purely Docker-level composition?
