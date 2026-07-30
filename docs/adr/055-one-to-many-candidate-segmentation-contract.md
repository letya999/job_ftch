---
title: "055 — One-to-many candidate segmentation contract"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 055 — One-to-many candidate segmentation contract

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-006 remains the generic typed-stage contract.
It is extended here with an explicit one-to-many boundary before extraction.

## Context

One source observation can contain several independent vacancies: an HTML
listing, an API array, a Telegram digest, or comments below a post. Processing
the parent as one candidate loses vacancies and lets fields from one listing
leak into another.

## Decision

Introduce an immutable `CandidateSpan` artifact with a parent observation
identity, ordinal, text, source evidence and optional contextual evidence.
`CandidateSegmentationNode` is the sole type-changing boundary from one raw
observation to zero or more spans. The pipeline expands spans independently;
failure, drop, or review for one span never affects a sibling or its parent
observation. A no-split observation produces one span containing its original
content, so existing single-vacancy sources preserve their behaviour.

The parent raw observation remains in the ledger and every downstream terminal
artifact retains both its parent observation ID and span ID.

## Consequences

- Candidate count and field correctness can be evaluated at segment level.
- Extraction and policy stages receive a single candidate rather than an
  ambiguous digest.
- The graph executor must deliberately support a type-changing fan-out; a
  tuple/list returned by an arbitrary same-type stage is not implicit routing.
