---
title: "105 — Paced Telegram channel delivery"
description: "Space channel posts without changing ingest frequency or LLM usage."
updated: 2026-11-03
---
# 105 — Paced Telegram channel delivery

**Status**: ACCEPTED
**Date**: 2026-11-03

## Context

The production ingest runs every 12 hours and can yield dozens of channel
posts. The previous 3.5-second send interval produced a burst. Reducing the
per-run send limit would defer most cards for another 12 hours or trigger the
fast recovery path intended for delivery failures.

## Decision

Set a production-only interval of 45 seconds between channel sends. Keep the
existing send limit and durable publish ledger. Manual chat replies retain
their short interval. No source or LLM run is added.

## Consequences

- A batch of 88 cards takes about 66 minutes to deliver.
- A bot restart resumes the pending publication window through the existing
  ledger and recovery path.
- The scheduler task remains occupied while sending; its 12-hour interval
  leaves ample room for the measured batch.
