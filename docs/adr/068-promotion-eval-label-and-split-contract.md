---
title: "ADR-068: Promotion evaluation requires repaired labels and a grouped split contract"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-068: Promotion evaluation requires repaired labels and a grouped split contract

**Status**: ACCEPTED
**Date**: 2026-07-15
**Extends**: ADR-051, ADR-052, ADR-058

## Context

The fixed-140 regression replay is a development slice, not an unbiased
promotion holdout. The larger historical dataset contains label defects and
exact-content duplicates. A deterministic content-only split can therefore
produce a plausible metric while still leaking source families or silently
claiming a temporal holdout that the input does not support.

## Decision

1. A promotion manifest is built from connected groups of exact content and
   source family (`source_kind/source_name`). Content duplicates always remain
   in one split; a source family cannot cross train, validation, and holdout.
2. Explicit regression IDs remain a development-only split. Any exact-content
   sibling of a regression item joins regression, but source-family overlap
   with regression is recorded rather than falsely presented as a clean
   holdout.
3. The manifest records temporal-partition capability. When all input rows
   lack a supported observation timestamp it records `unavailable`; a caller
   that requires temporal isolation fails explicitly.
4. Manifest validation can require clean labels. It rejects invariant and
   provider-error findings while allowing duplicates only when the manifest
   proves they do not cross split boundaries.
5. An unadjudicated provider failure can be repaired only by the narrow,
   deterministic `provider_failure_label_unknown` patch: it changes both
   labels to `unknown`, records the validator as repairer, and cannot assign a
   positive or negative semantic label. All other repairs remain adjudicated.
6. A pre-existing `relevant=1` label with missing/non-positive jobness can be
   repaired only by `positive_requires_jobness`, which restores `is_job=1`
   without changing relevance. It is a logical label invariant repair, not a
   new semantic positive judgment.
7. A promotion caller may require human-adjudicated provenance for every
   remaining positive. Deterministic invariant repair alone is intentionally
   insufficient for that gate.

## Consequences

- The current immutable historical dataset can still be used for diagnostic
  replays, but cannot be promoted with `--require-clean-labels`.
- A repaired append-only projection and real observation timestamps are
  prerequisites for the locked source/time/group-aware promotion holdout.
- No model threshold is selected from a dataset whose label or split contract
  has not passed these checks.
