---
title: "061 — Source family, observation kind and acquisition transport"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 061 — Source family, observation kind and acquisition transport

**Status**: ACCEPTED
**Date**: 2026-07-10

## Decision

The domain separates source family (`telegram`, `ats_api`, `career_web`, `rss`,
`rest_api`, `realtime`), observation kind (`vacancy_detail`, `listing`,
`message`, `comment`, `structured_record`) and acquisition transport (`http`,
`browser`, `telegram_api`, `webhook`, `websocket`). Browser is never a vacancy
source family.

Existing `SourceKind` remains a compatibility field while adapters migrate to
the typed identity. Core policy must use source family, not a generic
`career_site` value or host-name switches.

## Consequences

- Source-specific trust, freshness and extraction rules are calibratable.
- Listing/detail handling can be explicit.
- Existing persisted records remain readable during migration.
