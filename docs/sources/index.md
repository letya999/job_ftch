# sources Index

`docs/sources/`

Generated index for navigation. Edit source documents, then rerun `uv run python scripts/build_index_docs.py`.

## Files On This Level

- [Bot Parity Lab](bot_parity_lab.md) - Local defensive red-team layer for browser, network, realm, behavior, and identity-parity evidence. (Updated: 2026-08-04)
- [Browser lifecycle и teardown](browser_lifecycle.md) - Жизненный цикл browser-сессий в ingest: open_page, слот конкурентности, patchright cancellation-фиксы, session-bypass path, реапинг драйверов и полный teardown на выходе. (Updated: 2026-07-30)
- [Bypass и escalation path](bypass_and_escalation.md) - Adaptive bypass для ingest: failure signals, route axes, proxy/session/challenge boundaries и запреты. (Updated: 2026-08-05)
- [CAPTCHA provider rollout](captcha_provider_rollout.md) - Operational rollout for observed CAPTCHA/bot-protection handling: project wiring, browser setup, provider roles, and eval gates. (Updated: 2026-08-05)
- [Career-site runtime flow](career_site_runtime.md) - Фактический control-flow CareerSiteSource.fetch(): strategy init, site parser, generic search, monitor chain, discover/enrich, freshness, zero-yield taxonomy и teardown. (Updated: 2026-08-22)
- [Source Coverage Matrix](coverage_matrix.md) - Operational guidance for the heavy career-site boards that were the main (Updated: 2026-08-01)
- [Deadlines и concurrency](deadlines_and_concurrency.md) - Бюджеты времени и параллелизм ingest: source deadline scope, hard/soft/watchdog, per-source и global concurrency, loop-local limiters и их взаимодействие с teardown. (Updated: 2026-07-30)
- [Ingest stack: источники, assessment, monitors, scrapers, parsers, bypass](ingest_stack.md) - Полное, но компактное описание ingest-цепочки: от SourceSpec и pre-ingest assessment до RawItem, snapshots и pipeline. (Updated: 2026-07-28)
- [Публичный реестр источников](public_registry.md) - Откуда берётся live-список источников канала ai_engineer_jobs: runtime/DB, public-safe JSON и границы приватности. (Updated: 2026-08-11)
- [Search query ingest](search_query_ingest.md) - How career-site source URLs are expanded into keyword-search ingest targets. (Updated: 2026-08-22)
- [Source Setup](setup.md) - Prefer declarative source setup through `SourceSpec` entries inside a tenant YAML file. (Updated: 2026-08-01)
- [Source assessment](source_assessment.md) - Pre-ingest оценка источников: capability hints, freshness evidence, probe outcomes и границы ответственности. (Updated: 2026-07-28)
- [Справочник source stack](source_stack_reference.md) - Фактические модули ingestion/source stack: sources, monitors, scrapers, site parsers, assessment и bypass. (Updated: 2026-07-28)
