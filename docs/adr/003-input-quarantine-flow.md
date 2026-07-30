---
title: "003 - Input Quarantine Flow"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 003 - Input Quarantine Flow

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
Phase 2 requires explicit rejection reasons, URL/origin policy enforcement, and a quarantine flow for malformed or suspicious raw input. The original pipeline only handled validated `RawItem` objects and could silently lose observability when fixture payloads were malformed or when a source failed before yielding the first item.

## Decision
Add an explicit quarantine path:
- `SanitizeNode` raises structured rejections for malformed or suspicious `RawItem` values.
- Sources may emit `QuarantinedRawItem` records for payloads that fail before a valid `RawItem` exists.
- `Pipeline` routes both source-level quarantined records and node-level rejections into a quarantine sink.
- Source fetch failures are logged and emitted as quarantined records with a dedicated reason.

## Consequences
- (+) Phase 2 requirements are observable end-to-end instead of relying on silent drops.
- (+) Infrastructure no longer bypasses `RawItem` invariants with `model_construct`.
- (+) Debug fixtures can continue processing valid records after malformed ones.
- (-) The source contract is slightly richer: a source may now surface quarantined payloads in addition to valid items.
