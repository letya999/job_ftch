# Changelog

All notable changes to job_ftch are documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.5] - 2026-07-26

Production recipe and release-contract refresh. This release pins the
production graph to `evidence_v2_compact_prefilter`, ships the TF-IDF/logreg
relevance prefilter, narrows the MVP source scope to the verified 17-source set,
and aligns Docker, docs, tests, and eval artifacts around the same reproducible
runtime contract.

### Added

- **ADR-038 .. ADR-078 rollout**: the architecture record now covers the
  reranker/runtime split, production-equivalent eval contract, durable
  delivery/runtime degradation, observability separation, and the trainable
  TF-IDF + logistic regression relevance prefilter.
- **Recipe ledger and champion snapshot**: `scripts/eval/build_recipe_ledger.py`,
  `config/recipes/champion.yaml`, and `config/recipes/champion_artifact.json`
  pin the controlled 400-item PostgreSQL eval, graph hash, dataset hash,
  40-shot setup, and champion metrics.
- **Trainable relevance prefilter path**: the shipped production recipe now
  references `fixtures/prefilter/tfidf_logreg_v1.json` and
  `scripts/eval/train_relevance_prefilter.py` as the maintained pre-LLM gate.

### Changed

- **Production graph**: runtime and champion metadata now point at
  `config/pipelines/evidence_v2_compact_prefilter.yaml`
  (`08c5c5b7766adb2d7db630b403d302806b0b4f8e7d11d7df2968afa027cf4b22`).
- **Production runtime cost surface**: `config/runtime.prod.yaml` keeps
  `embedding_enabled=false`, `bgem3_enabled=false`, and `relevance_backend=keywords`;
  BGE-M3 remains out of the terminal decision path and is not preloaded into
  the production image.
- **MVP source scope**: docs and fixtures now describe the verified 17-source
  production set used by the current pipeline recipe and live-run artifacts.
- **Release checks**: local CI groups, source-spec fixtures, tenant/runtime
  tests, and MCP surface tests are aligned with the active prefilter graph
  instead of the historical postaccept-only path.

### Fixed

- **Docker production boundary**: the shared runtime image again runs under
  `appuser`, restores the browser font surface, and keeps adapter code baked
  into the image instead of relying on a production bind mount.
- **Snapshot safety**: snapshot reads are now fail-closed by default via
  `snapshot_fail_open=false`, so an unreadable snapshot store no longer silently
  disables run-to-run dedup for the whole source.
- **Schema/test fixtures**: `fixtures/specs/` is tracked again, restoring the
  level0-level2 e2e contracts consumed by the release test suite.

## [0.0.4] - 2026-06-18

The MVP-readiness release. Five new ADRs (033-037), five god-object /
hard-rule cleanups, an end-to-end snapshot-filter rewrite, and a
registry-driven adaptive bypass escalation engine. No new required
dependencies, no breaking API changes for adapter authors.

### Added (initial pass)

- **ADR-033 Plugin-based domain parsers**: every host-keyed `if/elif`
  in `infrastructure/sources/` is replaced by a registered
  `SiteParser` (per `domain_pattern`). New sites are added by dropping
  a module under `infrastructure/sources/site_parsers/` and decorating
  the parser with `@register_site_parser(name, domain_pattern=...)`.
  Four new plugins ship by default: `habr_career`, `hh`, `tbank_career`,
  `ozon_tech`. Yandex (`yandex_jobs`) was already present. Site parsers
  can opt out of the custom-parse path via the `has_custom_parse` flag
  (defaults-only providers like habr/hh/tbank/ozon do not short-circuit
  the default monitor chain).
- **ADR-034 Store auto-fallback**: `Settings.store_backend` defaults
  to `"auto"` instead of `"postgres"`. The new
  `application.registry.resolve_store_backend(settings)` resolver
  picks `postgres` (DSN available), `sqlite` (`JOB_FTCH_SQLITE_PATH`
  or `./data/jobs.db` writable), or `memory` (always works) in that
  order. `Settings()` instantiates without env vars, so
  `uv run job_ftch --help` works out of the box. Production stays
  explicit: `JOB_FTCH_STORE_BACKEND=postgres` plus a real DSN.
- **ADR-035 SecretStr policy**: 5 sensitive Settings fields
  (`openai_api_key`, `telegram_api_hash`, `telegram_proxy_password`,
  `langfuse_secret_key`, `qdrant_api_key`) are typed
  `SecretStr | None`. Pydantic masks their repr/str as
  `'**********'`, and `model_dump(mode="json")` does not leak the
  raw value. Call sites use `field.get_secret_value()`. New test
  `tests/test_settings_secrets.py` (5 tests).
- **ADR-036 Unified snapshot filter**: `SnapshotFilterNode` is now
  wired into the single-tenant path as the **2nd stage** (immediately
  after `SanitizeNode`), not just in the `tenants/*` multi-tenant path.
  `run_pipeline_from_settings()` generates a fresh `uuid4().hex` per
  CLI invocation and threads it through `build_nodes()` and
  `PipelineBuilder`. `Pipeline.run()` calls
  `await self._snapshot_filter.save_and_purge()` at the end of the
  run so the new snapshot is persisted and the 7-day TTL is applied.
  Single-tenant and multi-tenant paths now share the exact same node
  graph. New `tests/test_builder_uses_snapshot.py` (4 contract tests).
- **ADR-037 Adaptive scraping escalation policy**: new
  `FailureSignal` Protocol + `HeuristicFailureSignal` implementation
  classifies fetch failures into ok / rate_limit / captcha / blocked /
  timeout / parse_empty / unknown. CAPTCHA is binary (one match
  escalates immediately). blocked / rate_limit / timeout escalate after
  3 failures in 30 minutes for the same source. `parse_empty` does
  **not** escalate (a static page returning zero items is not a sign
  of a heavier browser). The tier list is built dynamically from the
  registry: if `cloakbrowser` is not installed, the `cloak` tier is
  silently absent. `Settings.adaptive_bypass_enabled` (default True,
  set false in CI / smoke runs) gates the manager.
  `career_site_source._try_escalate_bypass` now routes through
  `bypass_strategy.handle_failure()` (with a duck-typed fallback for
  third-party bypass implementations).
- **YAML schema validation in CI**: `scripts/validate_yaml_schemas.py`
  walks `config/*.yaml` and validates against
  `config/sources.schema.json`. Wired into a new
  `.github/workflows/validate-yamls.yml` job. Currently
  `continue-on-error: true` (informational) because some existing
  YAMLs use fields the schema marks as additional=false; flip to
  fail-fast after the next schema relaxation.
- **SanitizeNode enforcement through Protocol**: PipelineBuilder and
  Pipeline both check that the first stage is a `SanitizeNode` (or any
  class with `_is_sanitizer = True`). The marker opt-in is the
  documented way to plug a custom sanitiser.
- **sink atomicity contract tests**: `tests/test_sink_atomicity.py`
  pins the `JsonFileSink` contract from AGENTS.md: a crash before
  `flush()` preserves the previous output; the `.tmp` file is cleaned
  up; concurrent emits within the same process do not lose items;
  the on-disk file is never a half-written mix.
- **OTel funnel spans**: pipeline, hard_filter, post_type, and
  semantic_prefilter emit per-node funnel attributes
  (`job_ftch.node.result`, `job_ftch.item.stable_id`,
  `job_ftch.item.source_name`, `job_ftch.item.post_type`) gated by
  `tracing_capture_payloads`. Langfuse exporter wired through
  `[langfuse]` extra.
- **Datasets + scripts for evaluation**: `scripts/capture_dataset.py`,
  `scripts/auto_label_dataset.py`, `scripts/annotate_dataset.py`,
  `scripts/enrich_golden_dataset.py`, `scripts/run_experiment.py`,
  `scripts/pipeline_report.py`, `scripts/sync_to_langfuse.py`, plus
  `config/dataset_sources.yaml` and the initial
  `fixtures/dataset/labels.jsonl` (4MB golden dataset, 1412 samples).
- **`@register_site_parser` decorator + `resolve_site_parser` in
  registry** for runtime site-specific lookups (Yandex, plus the four
  defaults-only providers).

### Added (second pass, MVP cleanup completion)

- **CloakBrowser hardening (ADR-022 follow-up)**: `CloakBrowserBypass`
  now defaults to `humanize=True` (per-call mouse / keyboard / scroll
  look like a real user), `geoip=True` when a `proxy=` is configured
  (timezone + locale match the proxy exit IP), and `headless=False`
  (per CloakBrowser docs, some sites still detect headless even with
  the C++ patches). All three are class constants that can be
  overridden per call.
- **profile_inputs split**: the 758-line god object is decomposed
  into `profile_parsing.py` (pure-data helpers, ResumeExtractionPayload
  model, heuristic payload, build_candidate_profile_from_payload,
  build_profile_catalog, _detect_text_language_simple),
  `resume_extraction.py` (LLM-aware extraction,
  build_profile_from_resume_text_async, sync wrapper,
  merge_resume_profile, add_example_to_profile), and
  `ontology_enrichment.py` (live ontology shot enrichment, point ①
  per ADR-019 — `_build_shot_extraction_prompt`,
  `_enrich_ontology_from_shot`,
  add_example_to_profile_with_enrichment, load_resume_with_enrichment).
  `profile_inputs.py` is the thin orchestrator that re-exports the
  public API and hosts the three glue-level helpers
  (embed_profile_examples, remove_example_from_profile, list_examples).
- **tenant_runner split**: 1238 -> 1170 lines. `TenantRuntime`
  dataclass moved to `application/tenant_runtime.py`; the per-tenant
  file-system + in-process run lock moved to
  `application/tenant_locks.py` as `tenant_run_lock`. Both are
  re-exported from `tenant_runner` for backward compatibility.
- **Post-type keyword lists extracted to YAML**: ~100 hard-coded
  substring tokens across 4 categories (announcement, job_posting,
  candidate, spam) now live in `config/keyword_lists.yaml` and are
  loaded via `infrastructure/classifiers/keyword_lists.py` (mtime-cached).
  Operators extend or override the lists by editing the YAML — no code
  change. `tests/infrastructure/test_keyword_lists.py` (8 tests)
  pins the contract: tokens are present, lower-cased, cached by mtime.


### Changed

- **Store backend default**: `postgres` -> `auto`. See ADR-034.
- **5 new ADRs (033-037)** added to `docs/adr/README.md` index.
- **README pipeline diagram** corrected: `SnapshotFilterNode` is the
  2nd stage (after `SanitizeNode`), not between `Dedup` and
  `SemanticPrefilter`. Both single-tenant and multi-tenant paths now
  share the same node graph.
- **`Pipeline.__init__`** accepts an optional `snapshot_filter`
  parameter and `Pipeline.run()` calls
  `await self._snapshot_filter.save_and_purge()` at the end. Public
  API change is additive (default is `None`).
- **OTel tracing init** uses an `id(provider)` sentinel set instead of
  `hasattr(provider, "_is_job_ftch_configured")` duck-typing.

### Removed

- **`vault_auth.py` stub**: it was a NotImplementedError on every
  method but was registered as `@register_auth_provider("vault")`. Any
  operator that set `JOB_FTCH_AUTH_PROVIDER=vault` got
  `NotImplementedError` at runtime. Removed the stub and the
  load_extensions entry. Third-party plugins can re-register a real
  Vault integration via the entry-point mechanism.
- **`try/except TypeError` factory dispatch** in
  `resolve_bypass` and `create_source_from_spec` replaced with
  `inspect.signature`-based `_call_with_supported_kwargs`. Same
  external behaviour, explicit dispatch.

### Fixed

- **Pipeline model_copy dedup**: two identical `model_copy` blocks
  in `Pipeline.run()` (after the node chain and before the sink emit)
  consolidated into a single `Pipeline._inject_source_run_id` helper.
- **Pipeline contract test for the SanitizeNode guard**:
  `test_pipeline_builder_requires_sanitize_first` updated to match the
  new error message; Pipeline's `__init__` now also raises TypeError
  if a non-sanitizer is supplied directly.
- **Empty `url_filter` in CareerSiteConfig auto-detect**:
  `test_career_site_config_from_spec_detects_greenhouse` updated to
  expect `generic` for `parser_kind=auto` (per ADR-033: substring match
  was the very violation we removed).

### Tests

- **663 -> 681 passed, 10 skipped** (no new failures). New tests:
  - `tests/test_settings_secrets.py` (5)
  - `tests/test_sink_atomicity.py` (6)
  - `tests/test_adaptive_escalation.py` (10)

### Infrastructure

- TenantRunner god-object: 1652 -> 1230 lines (`TenantStore`
  extracted to `application/tenant_store.py` with verbatim
  re-export; remaining split into `tenant_store.py` /
  `candidate_profile_store.py` / `tenant_locks.py` deferred to a
  follow-up; current shape is a non-blocking improvement).
- Profile inputs split into `profile_parsing.py` /
  `resume_extraction.py` / `ontology_enrichment.py` /
  `profile_inputs.py` (orchestrator only).
- Extracted `Pipeline._inject_source_run_id` (Q4) and
  `infrastructure/observability/otel_setup._CONFIGURED_PROVIDER_IDS`
  sentinel (Q9).
- `infrastructure/stores/__init__.py` exposes
  `get_store_class(kind)` and `list_stores()` for plugin-lookup
  convenience (Q10).

## [0.0.3] - 2026-06-13

TD-002 evaluation harness (per ADR-032). Classification precision 0.98 /
recall 0.585 / FP 0.0228 over 1412 samples; extraction field match 0.81
over 51 gold samples. Dataset capture and annotation scripts.

## [0.0.2] - 2026-06-10

TD-013 run-based source snapshot (per ADR-031). Snapshot is row-level,
no whole-blob rewrites; run-based semantics adapt to every ingest
cadence.

## [0.0.1] - 2026-06-08

Phases 0-15. Initial pipeline + Telegram + LLM + job backends + search +
sink. Registry-driven plugin model (per ADR-007).

## [0.0.0] - 2026-05-20

Initial repository skeleton.
