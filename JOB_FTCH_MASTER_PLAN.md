# job_ftch Master Plan

## Purpose

This document defines the target shape of `job_ftch` as a top-tier open-source, library-first,
async ETL engine for job-like postings.

The goal is not to build a narrow "AI jobs parser". The goal is to build a lightweight,
high-signal, community-extensible ingestion and aggregation core that can:

- ingest heterogeneous sources
- cheaply reject noise before expensive processing
- normalize messy job-like content into stable records
- deduplicate and aggregate cross-source variants
- score and route records for review, storage, search, posting, and notifications

The project must remain:

- lightweight by default
- strict at module boundaries
- extensible without core rewrites
- measurable through benchmarks and contract tests
- useful with and without LLM backends

---

## Product Thesis

`job_ftch` should become a general-purpose engine for:

`multi-source ingest -> triage -> normalize -> dedup -> aggregate -> score -> route -> publish`

The durable value of the project is not one extractor or one source. The durable value is:

- a stable async pipeline core
- strong data contracts
- pluggable sources and sinks
- first-class aggregation
- profile-aware matching
- reproducible quality through fixtures and evals

AI/LLM roles, Telegram-first flows, and niche score policies should be shipped as profiles,
plugins, and config packs on top of the core.

---

## Hard Architectural Position

The core must stay small and boring.

The project should prefer:

- typed Protocol-based contracts
- entry-point plugin discovery
- declarative-first source configuration
- additive schema evolution
- deterministic logic before semantic logic
- reversible aggregation decisions
- extras-based optional dependencies

The project should avoid:

- central `if/elif` adapter dispatch in core
- plugin access to global `Settings`
- runtime-specific logic in the core package
- irreversible dedup merges
- LLM-only behavior with no deterministic fallback
- heavy orchestration dependencies in core

---

## Core Shape

The core payload family should stay compact:

1. `RawItem`
2. `JobDraft`
3. `JobRecord`
4. `JobGroup`

The pipeline should not explode into many top-level transition models. Instead, it should use
rich typed blocks inside stable payloads.

Recommended typed blocks:

- `SourceContext`
- `ClassificationSignals`
- `RoleBlock`
- `SkillBlock`
- `RequirementBlock`
- `ResponsibilityBlock`
- `AuthorityBlock`
- `CompensationBlock`
- `LocationBlock`
- `EmployerBlock`
- `CultureBlock`
- `RiskBlock`
- `MatchBlock`
- `QualityBlock`
- `AggregationBlock`
- `ProvenanceBlock`

This preserves strict type boundaries without making the pipeline brittle for contributors.

---

## Canonical Record Strategy

### `RawItem`

Purpose: source-facing raw envelope before extraction.

Minimum responsibilities:

- source identity
- source metadata
- raw text
- fetch timestamps
- source-specific metadata
- cheap pre-extraction signals

### `JobDraft`

Purpose: first structured representation after extraction.

Minimum responsibilities:

- extracted role/title
- extracted company
- extracted compensation
- extracted location
- extracted skills and requirements
- extracted culture/risk hints
- confidence and evidence where possible

### `JobRecord`

Purpose: stable normalized vacancy contract for storage, matching, search, and output.

Recommended sections:

- `identity`
- `source`
- `content`
- `role`
- `skills`
- `requirements`
- `responsibilities`
- `authority`
- `compensation`
- `location`
- `employer`
- `culture`
- `risk`
- `quality`
- `profile_matches`
- `aggregation`
- `provenance`

### `JobGroup`

Purpose: cross-source aggregate of likely same vacancy.

Responsibilities:

- group identifier
- member job ids
- canonical representative
- merge confidence
- source diversity
- lifecycle status
- provenance trail

Important rule:

- keep both `job_id` and `group_id`
- never collapse records irreversibly on first fuzzy match

---

## Canonical Vacancy Model

`JobRecord` should cover at least the following fields or equivalent nested blocks.

### Identity

- `job_id`
- `group_id`
- `schema_version`
- `source_record_id`

### Source

- `source_kind`
- `source_name`
- `source_url`
- `canonical_url`
- `fetched_at`
- `posted_at`

### Content

- `title_raw`
- `description_raw`
- `description_clean`
- `language`
- `languages_detected`

### Role

- `title_normalized`
- `role_family`
- `role_specialization`
- `role_track`
- `seniority`
- `leadership_level`
- `ic_or_manager`

### Skills

- `skills_explicit`
- `skills_inferred`
- `must_have_skills`
- `nice_to_have_skills`
- `tools_stack`
- `domain_knowledge`
- `soft_skills`

### Requirements

- `requirements_must`
- `requirements_nice`
- `years_experience`
- `education`
- `certifications`

### Responsibilities

- structured responsibility list

### Authority

- architecture ownership
- hiring responsibility
- stakeholder responsibility
- team leadership
- budget or product ownership

### Compensation

- `min_amount`
- `max_amount`
- `currency`
- `period`
- `gross_or_net`
- `bonus`
- `equity`

### Location

- `country`
- `region`
- `city`
- `timezone`
- `work_mode`
- `remote_restrictions`
- `relocation`
- `visa_support`

### Employer

- `company_name_raw`
- `company_name_normalized`
- `company_type`
- `industry`
- `domain`
- `project_types`
- `team_size_hint`

### Culture

- `culture_signals`
- `culture_summary`

### Risk

- `risk_signals`
- `risk_score`
- `risk_level`

### Quality

- `quality_score`
- `extraction_completeness`
- `review_reasons`

### Matching

- `profile_matches`
- `best_profile_id`
- `best_score`
- `routing_decision`

### Provenance

- extraction evidence
- normalization evidence
- merge evidence

---

## Candidate and Search Profiles

Candidate support is optional for the ingestion path but should be designed now.

### `Candidate`

- `candidate_id`
- `base_identity`
- `search_profiles`

### `SearchProfile`

- `profile_id`
- `name`
- `target_roles`
- `target_domains`
- `target_industries`
- `required_skills`
- `preferred_skills`
- `anti_preferences`
- `blocked_companies`
- `blocked_domains`
- `region_preferences`
- `work_mode_preferences`
- `salary_expectation`
- `language_preferences`
- `culture_preferences`
- `weights`
- `relevance_threshold`

One candidate may own multiple independent search profiles.

Matching should happen per profile, not against one global candidate blob.

---

## Plugin and Extension Strategy

The primary extension mechanism should remain Python entry points.

Recommended groups:

- `job_ftch.sources`
- `job_ftch.sinks`
- `job_ftch.stores`
- `job_ftch.extractors`
- `job_ftch.classifiers`
- `job_ftch.normalizers`
- `job_ftch.scorers`
- `job_ftch.notification_targets`

Plugin rules:

- plugin factories accept typed spec plus narrow dependencies
- plugins return Protocol-compatible implementations
- plugins do not receive the whole `Settings` object
- plugins do not inspect or mutate the pipeline graph
- heavy dependencies remain optional extras

For community adoption, each plugin type needs:

- a tiny template
- contract tests
- metadata manifest
- example fixture
- failure behavior docs

---

## Declarative-First Source Strategy

The project should support two ways to add sources.

### Declarative path

For the common case:

- `CareerSiteConfig`
- YAML or manifest-driven extraction
- reusable selectors and extraction hints
- source auth kept separate from source config

### Imperative path

For the hard case:

- self-registered adapter module
- entry-point plugin package
- contract-tested source implementation

The declarative path should cover the majority of career site integrations.

---

## Schema and Contract Governance

Pydantic models are not enough on their own. Public contracts must be explicit.

The project should export:

- `RawItem.schema.json`
- `JobDraft.schema.json`
- `JobRecord.schema.json`
- `JobGroup.schema.json`

Rules:

- additive-first evolution
- breaking changes require migration guidance
- public schema ids must be versioned
- fixtures must exist for each exported schema
- compatibility tests must run in CI

Recommended public evolution style:

- add optional fields freely
- deprecate before removing
- avoid renaming stable fields without migration support

---

## Ontology and Multilingual Strategy

Version 1 should fully support:

- `ru`
- `en`

The architecture should be ready for:

- `kk`
- `uz`

Rules:

- canonical ids and canonical enum values in English
- multilingual aliases and surface forms
- language detection before normalization
- role and skill normalization via ontology plus alias tables
- semantic reranking as an optional layer, not the only layer

The project should not invent the whole labor taxonomy from scratch.

Recommended external anchors:

- ESCO for multilingual occupations and skills
- optional O*NET and ISCO enrichment
- `schema.org/JobPosting` alignment for export-facing interoperability

---

## Dedup and Aggregation Strategy

Dedup must be multi-layered.

### Layer 1: Seen dedup

Cheap exact checks:

- source message id
- canonical URL
- raw URL normalization
- source-specific stable key

### Layer 2: Near-dup candidate generation

Blocking keys built from normalized:

- company
- title
- location
- salary window
- date window

### Layer 3: Fuzzy or semantic similarity

Applied only after blocking.

Signals may include:

- normalized title similarity
- company similarity
- location consistency
- compensation consistency
- text similarity
- extracted skill overlap

### Layer 4: Cross-source aggregation

Output is not destructive dedup. Output is:

- source-level `JobRecord`
- cross-source `JobGroup`

This preserves replayability and auditability.

---

## Scoring Model

Scoring must be policy-driven and decomposed into separate axes.

Required axes:

- `RelevanceScore`
- `RiskScore`
- `QualityScore`
- `AggregationConfidence`

For profile matching:

- hard gates
- role fit
- skills fit
- domain fit
- context fit
- culture soft fit
- penalties

Output per profile:

- component scores
- final score
- decision
- explanation

---

## Top-Tier Matching Funnel

The project should use a top-tier 10-node funnel for matching and routing.

This exceeds the minimum 8-node target and separates concerns cleanly.

### Node 0. `SanitizeNode`

Input:

- `RawItem`

Output:

- `RawItem`

Responsibilities:

- normalize whitespace and HTML noise
- remove obviously broken payloads
- bound text size safely
- quarantine unsafe or policy-violating input

Why first:

- this is a hard project invariant

### Node 1. `SourceContextNode`

Input:

- sanitized `RawItem`

Output:

- enriched `RawItem`

Responsibilities:

- detect language
- classify source family
- attach source trust hints
- attach source parsing hints
- attach cheap source-level metadata used downstream

### Node 2. `PostTypeClassificationNode`

Input:

- contextual `RawItem`

Output:

- enriched `RawItem`

Responsibilities:

- classify item into:
  - `job_posting`
  - `candidate_seeking`
  - `announcement`
  - `spam`
  - `unknown`
- keep confidence and reason hints

Important:

- this is not relevance scoring
- this only answers "what kind of thing is this?"

### Node 3. `HardFilterNode`

Input:

- classified `RawItem`

Output:

- filtered `RawItem`

Responsibilities:

- drop obvious non-job content
- enforce allowed language and source constraints
- apply blocked-company and blocked-domain gates
- reject cheap suspicious patterns before expensive work

### Node 4. `DedupCandidateNode`

Input:

- filtered `RawItem`

Output:

- dedup-annotated `RawItem`

Responsibilities:

- exact seen-check
- stable key generation
- near-dup candidate lookup
- attach dedup hints for later aggregation

Important:

- this stage should not over-merge

### Node 5. `SemanticPrefilterNode`

Input:

- dedup-annotated `RawItem`

Output:

- profile-annotated `RawItem`

Responsibilities:

- cheap role and domain screening
- multi-profile preliminary scoring
- discard clearly off-target items
- allow `relevant` and `uncertain` to pass

Important:

- keep this cheap
- avoid full extraction for obvious misses

### Node 6. `ExtractionNode`

Input:

- relevant or uncertain `RawItem`

Output:

- `JobDraft`

Responsibilities:

- structured extraction of role, company, location, compensation
- separate skills, requirements, responsibilities, authority
- capture raw evidence and confidence where possible

Important:

- only here does the type change from raw to job-like structured data

### Node 7. `NormalizationNode`

Input:

- `JobDraft`

Output:

- `JobRecord`

Responsibilities:

- canonicalize titles
- canonicalize skills
- map domains and industries
- normalize geography and work mode
- normalize compensation units and currency markers
- attach ontology ids and aliases

### Node 8. `AggregationNode`

Input:

- normalized `JobRecord`

Output:

- aggregation-aware `JobRecord`

Responsibilities:

- assign or resolve `group_id`
- compare against near-dup candidates
- attach merge confidence
- preserve source-level record identity
- update lifecycle and provenance hints

### Node 9. `MatchScoringNode`

Input:

- aggregation-aware `JobRecord`

Output:

- scored `JobRecord`

Responsibilities:

- score against all active search profiles
- compute component scores
- compute final per-profile scores
- keep explanations
- determine best profile and best score

### Node 10. `RiskAndQualityNode`

Input:

- scored `JobRecord`

Output:

- fully evaluated `JobRecord`

Responsibilities:

- compute `RiskScore`
- compute `QualityScore`
- distinguish quality issues from risk issues
- assign review reasons

### Node 11. `RoutingNode`

Input:

- fully evaluated `JobRecord`

Output:

- accepted, review, or rejected routed outcome

Responsibilities:

- route to main sink, review sink, rejected sink, posting sink, notification sink
- apply policy by score, risk, quality, and aggregation confidence
- produce deterministic reason codes

---

## Matching Semantics

The matching funnel should use the following logic families.

### Hard gates

- allowed language
- allowed source kind
- blocked company or domain
- work mode constraints
- employment type constraints
- salary floor if strict
- seniority bounds if strict

### Role fit

- exact title alias hit
- normalized title hit
- role family overlap
- role track overlap
- optional semantic role similarity

### Skill fit

- must-have coverage
- preferred skill coverage
- tool overlap
- adjacent-skill bonus
- domain-knowledge bonus

### Domain fit

- domain overlap
- industry overlap
- project-type overlap

### Context fit

- region fit
- timezone fit
- work mode fit
- compensation fit

### Culture fit

- soft bonus only at first

### Penalties

- risk penalties
- weak-information penalty
- probable duplicate penalty if unresolved

---

## Node Contract Policy

All nodes should converge toward a standard contract shape.

Each node should declare:

- input schema
- output schema
- validator policy
- reason codes
- metrics names
- decision semantics

Logical outcomes:

- `accept`
- `drop`
- `review`
- `error`

Even if the runtime currently returns `item | None`, the reason model and metrics model should
already follow this explicit decision structure.

---

## Evaluation and Benchmarks

Evaluation must be built into the architecture.

Minimum benchmark layers:

- extraction field accuracy
- normalization accuracy
- profile match precision and recall
- dedup precision and recall
- aggregation correctness
- routing precision
- latency per stage
- cost per item for LLM-enabled flows

Datasets should include:

- Telegram channels
- Telegram groups and comments
- career sites
- `ru`
- `en`
- cross-source duplicates
- borderline false-positive and false-negative cases

---

## Observability

The project should standardize metrics and reasons from the start.

Recommended measurements:

- per-stage latency
- per-stage drop counts
- per-stage review counts
- extraction completeness
- dedup hit rate
- near-dup candidate rate
- aggregation merge rate
- high-risk rate
- low-quality rate
- per-profile acceptance rate

---

## Delivery Roadmap

### Phase 1. Architecture and contracts

- approve this plan
- write ADRs for payload family, plugin API, schema policy, aggregation model, multilingual strategy
- stabilize reason codes and contract boundaries

### Phase 2. Canonical data model

- finalize `RawItem`, `JobDraft`, `JobRecord`, `JobGroup`
- add public JSON Schema export
- keep compatibility bridge with current models

### Phase 3. Matching funnel rollout

- land 10-node funnel shape in builder and contracts
- keep deterministic cheap filters ahead of extraction
- separate relevance, risk, quality, and aggregation logic

### Phase 4. Ontology and normalization

- bootstrap ESCO-backed role and skill alias layer
- add `ru/en` normalization pack
- make `kk/uz` extension-ready

### Phase 5. Aggregation

- exact dedup
- near-dup blocking
- `JobGroup`
- provenance-preserving merge flow

### Phase 6. Plugin SDK

- source template
- sink template
- scorer template
- normalizer template
- plugin metadata contract
- contract test kit

### Phase 7. Evaluation and observability

- benchmark fixtures
- regression suite
- per-stage metrics
- score quality dashboards

### Phase 8. Community hardening

- contributor documentation
- compatibility matrix
- support tiers
- migration policy
- example plugins

---

## Success Criteria

The plan is successful when `job_ftch` is:

- installable as a lightweight core library
- extensible through entry-point plugins without core edits
- capable of high-signal 10-node matching and routing
- able to preserve source-level records while aggregating cross-source duplicates
- governed by public schemas and compatibility policy
- measurable through evals, fixtures, and metrics

This is the path to a top-tier open-source ETL engine for job aggregation.
