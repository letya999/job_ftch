# 024 — Canonical Job Contract And Top-Tier Matching Funnel

**Status**: ACCEPTED
**Date**: 2026-06-12

> Updated: 2026-06-12. Status changed to ACCEPTED; implementation
> underway on branch feat/semantic-job-pipeline.

## Context

`job_ftch` started as a lightweight async pipeline for collecting vacancies from Telegram and
career sites into structured JSON. The project has since grown beyond simple extraction:

- multiple heterogeneous sources
- configurable relevance profiles
- deduplication and cross-source grouping
- downstream posting and notifications
- growing demand for multilingual normalization
- growing demand for profile-aware job matching

The current architecture already has the right spine:

- hexagonal boundaries
- typed `Stage[In, Out]` contracts
- sanitize-first invariant
- registry-based extension model
- optional aggregation and downstream routing

However, the project still lacks one explicit target contract for:

- the canonical shape of a normalized vacancy
- the intended relationship between raw records, normalized records, and aggregates
- the matching funnel that decides whether a raw item becomes a routed job record
- the separation between relevance, risk, quality, and aggregation confidence

Without that target, the core risks drifting into one of two bad states:

1. many ad hoc fields and node-specific heuristics with no stable exported contract
2. too many fragile intermediate payload types, making community extension harder

## Decision

Adopt the following target model for the next major architecture pass.

### 1. Compact core payload family

The canonical payload family is:

1. `RawItem`
2. `JobDraft`
3. `JobRecord`
4. `JobGroup`

The pipeline must not proliferate many mandatory top-level transitional payload classes.
Instead, rich typed blocks should live inside these stable payloads.

Recommended internal blocks include:

- source context
- classification signals
- role
- skills
- requirements
- responsibilities
- authority scope
- compensation
- location
- employer
- culture
- risk
- quality
- profile matches
- provenance

### 2. Canonical vacancy contract

`JobRecord` becomes the public normalized vacancy contract for storage, search, matching,
notifications, and downstream export.

It must cover at least:

- identity
- source
- content
- role
- skills
- requirements
- responsibilities
- authority
- compensation
- location
- employer
- culture
- risk
- quality
- profile matches
- aggregation
- provenance

Raw and normalized views should coexist where useful. The system should preserve:

- raw extracted value
- normalized value
- confidence
- evidence or provenance hints

### 3. Cross-source aggregation is first-class

Deduplication is not only a drop operation.

The system must preserve both:

- source-level `JobRecord`
- cross-source `JobGroup`

Rules:

- `job_id` and `group_id` are distinct
- fuzzy similarity must not irreversibly merge records on first pass
- group membership should preserve source appearances and merge confidence

### 4. Top-tier matching funnel

The target matching and routing funnel is a 10+ node shape:

1. `SanitizeNode`
2. `SourceContextNode`
3. `PostTypeClassificationNode`
4. `HardFilterNode`
5. `DedupCandidateNode`
6. `SemanticPrefilterNode`
7. `ExtractionNode`
8. `NormalizationNode`
9. `AggregationNode`
10. `MatchScoringNode`
11. `RiskAndQualityNode`
12. `RoutingNode`

This should be interpreted as four logical rings:

- intake
- understanding
- canonicalization
- decision and delivery

The only required cross-type extraction boundary remains:

- `RawItem -> JobDraft`

Later normalization and aggregation steps operate on job-shaped records, not raw text.

### 5. Separate scoring axes

The architecture must keep the following axes distinct:

- `post_type`
- `relevance`
- `risk`
- `quality`
- `aggregation_confidence`

These should not be collapsed into one opaque score.

For profile-aware matching, per-profile outputs must include:

- hard-pass result
- component scores
- final score
- decision
- explanation

### 6. Extension policy

The project continues to use registry plus entry-point extensibility.

Core logic should remain small and stable while fast-changing behavior moves into:

- profiles
- plugin packages
- declarative source configs
- ontology or alias packs
- eval fixtures

Community contributors should be able to add sources, sinks, normalizers, and scorers
without editing core dispatch code.

### 7. Contract governance

Public JSON schemas should be exported for:

- `RawItem`
- `JobDraft`
- `JobRecord`
- `JobGroup`

Schema evolution policy is additive-first:

- add optional fields safely
- deprecate before removal
- document breaking changes explicitly

## Consequences

- (+) The project gains one explicit target shape for future domain and pipeline work.
- (+) Matching, routing, and aggregation can evolve without losing the library-first core.
- (+) Community contributors get a clearer mental model: stable payload family, pluggable logic.
- (+) Public schema export becomes a natural extension of the domain contract.
- (-) Some existing assumptions such as "all post-extraction nodes are same-type `Job -> Job`"
  will need clarification or compatibility shims during rollout.
- (-) The codebase will need a migration period where current `Job` models coexist with the
  planned `JobDraft` and `JobRecord` shape.
- (-) More explicit scoring separation increases implementation surface area and test burden.
- (-) The funnel adds decision clarity, but also requires stronger fixtures, reason codes,
  and benchmark discipline.
