# Architecture Master Plan

- Read alongside root `JOB_FTCH_MASTER_PLAN.md` and ADR `docs/adr/024-canonical-job-contract-and-matching-funnel.md`.
- Product direction: `job_ftch` is a library-first async ETL core for job-like postings, not a niche AI-only parser.
- Durable value is the chain:
  multi-source ingest -> triage -> normalize -> dedup -> aggregate -> score -> route -> publish.
- Core should stay small and boring:
  typed contracts, low default infra, deterministic gates before semantic work, extras for heavy deps.
- Prefer a compact payload family:
  `RawItem`, `JobDraft`, `JobRecord`, `JobGroup`.
  Rich structure belongs inside typed blocks, not in many mandatory top-level transition classes.
- Canonical vacancy record must cover:
  identity, source, content, role, skills, requirements, responsibilities, authority, compensation,
  location, employer, culture, risk, quality, profile matches, aggregation, provenance.
- Cross-source aggregation is first-class:
  preserve both source-level record identity and aggregate `JobGroup` identity.
  Avoid irreversible fuzzy merges on first pass.
- Public contracts should not live only as Pydantic runtime models:
  export JSON Schemas and treat schema evolution as additive-first.
- Multilingual strategy:
  English canonical ids, multilingual aliases, `ru/en` first, `kk/uz` extension-ready.
- Fast-changing logic should move out of core into:
  declarative configs, profiles, plugin packages, ontology packs, eval fixtures.
