---
title: "062 — Unified evidence and confidence aggregation"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 062 — Unified evidence and confidence aggregation

**Status**: ACCEPTED
**Date**: 2026-07-10

## Decision

Policy-relevant nodes emit immutable `EvidenceAtom` values. An atom records a
claim, polarity, strength, calibrated producer reliability, provenance,
source family, observation identity, evidence reference and independence key.

The policy layer groups atoms by claim and independence key. Signals in one
group count only once. Independent groups increase evidence coverage with
diminishing returns. Support and contradiction affect belief separately;
conflict lowers certainty. Missing evidence is `unknown`, not contradiction.

Producer self-reported confidence is not a calibrated probability. Thresholds
and producer reliabilities are versioned policy configuration and are calibrated
by source/time holdout evaluation.

## Consequences

- Different nodes cannot silently compete on unrelated score scales.
- Evidence is replayable and auditable.
- Adding independent proven signals increases certainty without double counting.
