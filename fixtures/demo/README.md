---
title: "Local two-tenant Docker demo"
description: "Synthetic fixtures for exercising two independent job_ftch tenants in Docker."
updated: 2026-09-06
---

# Local ten-tenant Docker scheduler demo

This fixture-only demo keeps the existing `ai_jobs` tenant untouched and adds
ten isolated search lanes. Five run every 15 minutes and five every 30 minutes.
The live compose service starts the existing `pipeline --daemon` scheduler;
each tick evaluates tenant schedules and stores data in its own SQLite files.

The lanes cover applied AI engineering, AI product/program work, data platforms,
cloud reliability, analytics, application security, developer experience,
robotics, applied research and technical solutions. Each has its own profile,
skills, anti-preferences and source pair.

All tenants use the heuristic provider and local JSON sources, so the live demo
does not need Telegram credentials, OpenAI, Postgres or Qdrant. Synthetic resumes
are represented by the profile descriptions and examples in `profiles/`.
