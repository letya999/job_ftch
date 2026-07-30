---
title: "ADR-067: Preserve jobness evidence from the compact relevance judge"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-067: Preserve jobness evidence from the compact relevance judge

**Status**: ACCEPTED
**Date**: 2026-07-15
**Extends**: ADR-056, ADR-062, ADR-065, ADR-066

## Context

ADR-065 made the compact judge return explicit `is_job`, role relation, and
responsibility-fit facts.  The initial typed adapter only materialized the
derived profile-relevance fact.  When an inexpensive pre-LLM jobness producer
is uncertain, `DecisionNode` therefore defers an item even when the compact
judge has explicitly returned `is_job=yes` with numbered posting evidence.

The first post-accept replay exposed this loss mode on two career-site
positives: the compact judge supported both relevance and jobness, but the
jobness claim did not cross the terminal policy because it had been discarded
at the transport boundary.  Recovering that fact must not turn a model
response into a terminal decision or lower the evidence requirements.

## Decision

1. In compact-evidence mode, materialize a separate typed `IS_JOB` atom from
   the judge's explicit `is_job` field in addition to the profile-relevance
   atom.
2. `is_job=yes` produces supporting jobness evidence only when the response
   cites at least one positive posting snippet.  `is_job=no` produces
   contradicting jobness evidence only when it cites a negative snippet (or a
   positive snippet when that is the schema's available non-job reference).
   `unknown` and uncited values produce no jobness atom.
3. The two atoms are separate claim transports with independent claim keys;
   they are not two votes for one claim.  Both retain the same LLM provenance,
   evidence references, versioned reliability, and candidate identity.
4. `DecisionNode` remains the only terminal owner.  Role-adjacent or
   responsibility-contradicting output remains negative profile evidence even
   if the text is correctly identified as a vacancy.
5. Cache reconstruction produces the same atoms as a fresh response.

## Consequences

- The candidate graph can recover true vacancies whose cheap jobness signal is
  unknown without another LLM call or broader acceptance rule.
- A non-target vacancy can still be a positive `IS_JOB` claim and a negative
  profile-relevance claim; this distinction prevents recall recovery from
  becoming a precision regression.
- Promotion remains conditional on a fresh frozen replay with the existing
  precision/recall/cost gates.

## Validation

Targeted tests prove that a cited compact `is_job=yes` response creates a
separate supporting `IS_JOB` atom and can resolve an otherwise unknown cheap
jobness claim through `DecisionNode`; an adjacent-role response remains
negative profile evidence. The fixed-140 replay recorded in ADR-066 satisfies
`precision=.9412`, `recall=.8889`, and `F1=.9143` at `$0.0560932` decision-path
cost, so the typed transport correction did not regress the frozen quality
gate.
