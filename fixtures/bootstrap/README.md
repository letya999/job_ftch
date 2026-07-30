---
title: "Dev environment bootstrap fixtures"
description: "Fixtures and configs used to bootstrap local job_ftch development."
updated: 2026-07-26
---
# Dev Environment Bootstrap

This directory contains fixtures and configs required to bootstrap a local development environment for the `job_ftch` Telegram bot and ingestion pipelines.

## Contents
- `tenant_ai_jobs.yaml`: A complete tenant configuration for testing, pre-populated with realistic test sources (Telegram channels and career sites).
- `test_user.json`: A JSON fixture for registering a test Telegram user into the database for debugging without requiring full interaction flow.

See the documentation in `docs/sources/setup.md` and `docs/quickstart.md` for full instructions.
