---
title: "Domain Model Map"
description: "Полный индекс `job_ftch/domain/*` на текущий момент."
updated: 2026-07-28
---
# Domain Model Map

Полный индекс `job_ftch/domain/*` на текущий момент. Это не замена подробным
entity-docs, а карта покрытия, чтобы ни один актуальный модуль домена не
оставался невидимым в документации.

## Core vacancy payloads

| Module | Role |
|---|---|
| `source_spec` | декларативные `SourceSpec` и runtime source config |
| `runtime_source` | runtime overlay для источников |
| `runtime_profile` | runtime overlay для профилей кандидата |
| `ingest_models` | промежуточные ingest DTO и candidate discovery models |
| `models` | канонические `RawItem`, `JobDraft`, `JobRecord` и связанные blocks |
| `structured_vacancy` | typed structured vacancy helpers beyond the base record |
| `job_group` | `JobGroup` и cross-source aggregate contract |
| `job_quality` | quality/evidence completeness primitives |
| `relevance` | relevance outcomes and match-facing enums |
| `resolution` | deferred/review resolution payloads |
| `triage` | early triage values and decision hints |
| `presentable` | presentation-oriented payload fragments |

## Evidence, assessment, and provenance

| Module | Role |
|---|---|
| `assessment` | source/item assessment values |
| `source_assessment` | pre-ingest source capability assessment models |
| `evidence` | evidence atoms, bundles, and claim-level helpers |
| `observation` | immutable observation/log-like payloads |
| `lineage` | provenance and lineage tracking |
| `source_identity` | canonical source/item identity helpers |
| `source_outcomes` | per-source run outcomes and counters |
| `source_health` | source health state exposed to runtime controls |
| `bgem3_card` | dense/sparse BGEM3 evidence card payloads |
| `shot_extraction` | shot-related extraction payloads |
| `ontology_graph` | ontology graph nodes/edges and graph-facing domain contracts |
| `feedback` | feedback-facing domain payloads |

## Candidate, policy, and delivery

| Module | Role |
|---|---|
| `candidate` | candidate-level domain values |
| `profile` | profile payloads and public candidate profile shapes |
| `filter_profile` | filtering/matching profile primitives |
| `company` | company normalization/value objects |
| `enrichment` | post-accept enrichment payloads |
| `experiment` | evaluation/experiment domain metadata |
| `contracts` | shared small domain contracts/enums |
| `dedup` | dedup keys, fingerprints, merge helpers |
| `outbox` | durable delivery record and idempotency key contract |
| `quarantine` | quarantine payloads and reasons |
| `rejected` | rejected-item contract |
| `tenant` | tenant identity/config-facing domain values |
| `site_models` | site-classification and site intelligence payloads |

## Related detailed docs

- [RawItem](raw_item.md)
- [JobDraft](job_draft.md)
- [JobRecord](job_record.md)
- [JobGroup](job_group.md)
- [SourceSpec](source_spec.md)
- [CandidateProfile](candidate_profile.md)

## Top-level classes and enums

| Module | Classes / enums |
|---|---|
| `assessment` | `WorkState`, `AssessedJob`, `DecisionResult` |
| `candidate` | `CandidateIdentity`, `CandidateResumeSnapshot`, `CandidateProfile` |
| `dedup` | `DedupKeyKind`, `DuplicateRejectionReason`, `RememberedDedupKey`, `DuplicateRecord` |
| `enrichment` | `EnrichmentTask` |
| `evidence` | `ClaimKind`, `EvidencePolarity`, `EvidenceAtom`, `ClaimParameters`, `ClaimAssessment`, `EvidenceBundle` |
| `experiment` | `RelevanceCard` |
| `feedback` | `FeedbackVerdict`, `FeedbackAudience`, `VacancyFeedback`, `FeedbackJobTally`, `FeedbackSummary` |
| `filter_profile` | `FilterProfile` |
| `ingest_models` | `IngestItemStatus`, `DiscoveredCandidate` |
| `job_group` | `SourceAttribution`, `JobGroup` |
| `job_quality` | `ExtractionRejectionReason`, `JobReviewReason`, `JobValidationRejectionReason` |
| `lineage` | `JobLineage` |
| `models` | `SourceKind`, `LanguageCode`, `WorkMode`, `EmploymentType`, `Seniority`, `PostType`, `JobExtractionStatus`, `MatchDecision`, `RiskLevel`, `JobStatus`, `CompensationPeriod`, `SkillTag`, `AuthorityScope`, `CompensationRange`, `ProfileMatchScore`, `ProvenanceTrail`, `RawItem`, `CandidateSpan`, `EvidenceProvenance`, `PageKind`, `StructuredSourceEvidence`, `JobnessDecision`, `OntologySnapshot`, `JobDraft`, `Job`, `JobRecord` |
| `observation` | `ObservationLedgerEntry` |
| `ontology_graph` | `OntologyNode`, `OntologyEdge`, `OntologyEvidence`, `ExtractedOntologyClaim`, `ExtractedRoleSkillEdge`, `ShotOntologyExtraction`, `MaterializedOntologyTerms`, `CompiledOntologyTerm`, `CompiledOntologyRelation`, `CompiledOntology`, `OntologyTermStat`, `ShotOntologyGraph` |
| `outbox` | `OutboxState`, `OutboxRecord` |
| `presentable` | `PresentableJob` |
| `profile` | `ProfileWeights`, `CompensationExpectation`, `SearchProfile`, `ProfileCatalog` |
| `quarantine` | `RawItemRejectionReason`, `QuarantinedRawItem` |
| `rejected` | `RejectedOutcome`, `RejectedItem` |
| `relevance` | `RelevanceClassification`, `RelevanceEvidenceClassification` |
| `resolution` | `ResolutionTask` |
| `runtime_profile` | `ManagedCandidateProfile` |
| `runtime_source` | `RuntimeSourceRecord` |
| `shot_extraction` | `RelevanceKeyword`, `ShotExtraction` |
| `site_models` | `DiscoveredPostingPayload`, `ScrapedPostingPayload`, `MonitorResult` |
| `source_assessment` | `AssessmentConfidence`, `SourceEvidence`, `SourceCapabilities`, `FreshnessAssessment`, `SourceAssessmentResult`, `SourceIngestState` |
| `source_health` | `SourceHealth` |
| `source_identity` | `SourceFamily`, `ObservationKind`, `AcquisitionTransport`, `SourceIdentity` |
| `source_spec` | `BaseSourceSpec`, `TelegramChannelSpec`, `TelegramGroupSpec`, `TelegramCommentsSpec`, `DeclarativeHtmlSpec`, `CareerSiteSpec`, `LocalFixtureSpec`, `CursorPagination`, `OffsetPagination`, `LinkHeaderPagination`, `RestAPISourceSpec`, `BrowserSourceSpec`, `RSSFeedSourceSpec`, `TelegramRealtimeSourceSpec`, `LeverSourceSpec`, `WebhookSourceSpec`, `WebSocketSourceSpec` |
| `tenant` | `OutputSpec`, `ScheduleSpec`, `TenantConfig`, `TenantInfo` |
| `triage` | `TriageRejectionReason` |
