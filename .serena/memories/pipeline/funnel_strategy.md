# Funnel Strategy

- Target matching funnel is high-signal and multi-node, with cheap gates before expensive extraction.
- Intended node order:
  `SanitizeNode`
  `SourceContextNode`
  `PostTypeClassificationNode`
  `HardFilterNode`
  `DedupCandidateNode`
  `SemanticPrefilterNode`
  `ExtractionNode`
  `NormalizationNode`
  `AggregationNode`
  `MatchScoringNode`
  `RiskAndQualityNode`
  `RoutingNode`
- Interpret the flow as four logical rings:
  intake, understanding, canonicalization, decision and delivery.
- Important separation:
  post type answers "what is this";
  relevance answers "is it relevant for a profile";
  risk answers "is it suspicious";
  quality answers "is it complete and useful";
  aggregation confidence answers "how safely can it join a group".
- Keep the only hard raw-to-job type boundary at extraction time:
  `RawItem -> JobDraft`.
  Downstream nodes should operate on job-shaped data, not go back to raw text heuristics.
- Semantic prefilter must stay cheap.
  Its purpose is to avoid expensive extraction for obvious misses, not to replace final scoring.
- Dedup should be layered:
  exact seen check first, blocking keys second, fuzzy or semantic similarity only on candidates.
- Routing should be deterministic by policy and reason codes, not by implicit score magic.
