# Plan: Adaptive Extraction & Scraping Escalation
**Date:** 2026-06-09

## Goal
To eliminate manual tuning of scraping configurations (like bypass mechanisms and monitor choices) by introducing a self-healing, adaptive crawler that escalates tactics based on server responses.

## Phase 1: Tiered Bypass Escalation
1. **Define Tiers in `BypassStrategy`:**
   - Modify `application.contracts.BypassStrategy` to include an awareness of its tier/cost.
   - Tier 0: NoopBypass
   - Tier 1: CurlBypass (TLS masquerading)
   - Tier 2: StealthBrowserBypass (Playwright with JS masking)
   - Tier 3: CloakBrowserBypass (C++ compiled stealth)
2. **Implement `auto` resolver:**
   - Modify `resolve_bypass` to return an `AdaptiveBypassManager` when `bypass: auto` is specified.
3. **Escalation Logic in Source/Scraper:**
   - Catch specific exceptions (`httpx.HTTPStatusError` for 403, Cloudflare challenge DOM indicators).
   - If blocked, `AdaptiveBypassManager` iterates to the next tier and triggers a retry mechanism inside `CareerSiteSource` or `CompositeSource`.

## Phase 2: Heuristic Auto-Sniffer Monitor
1. **Add `monitor: auto` flag:**
   - In the pipeline config, support `monitor: auto`.
2. **First pass (`dom`):**
   - Run the standard `dom` monitor. If it finds valid URLs, stop and emit.
3. **Second pass (`api_sniffer` heuristic mode):**
   - If `dom` returns 0 jobs (typical of SPA sites like GetMatch), launch the browser with network interception.
   - Capture all HTTP response bodies where `Content-Type` is `application/json`.
   - **Filter Algorithm:** Analyze JSONs for array structures containing keys like `id`, `title`, `salary`, `description`. Use a fast fuzzy-match or a lightweight LLM prompt to identify the actual job feed payload.
4. **Integration:** Yield the parsed JSON directly into the pipeline without needing an HTML scraper step.

## Phase 3: Strategy Caching Layer
1. **Schema Extension:**
   - Extend the `Store` schema (SQLite/Postgres) with a new table `source_strategies`.
   - Columns: `domain` (str), `working_monitor` (str), `working_bypass` (str), `last_success_at` (datetime).
2. **Fast-path Execution:**
   - At the start of `fetch()`, check the cache. If a cached strategy exists and is fresh (e.g., < 30 days old), jump straight to that tier instead of starting from Tier 0.
3. **Cache Invalidation:**
   - If a cached strategy fails, invalidate it and fall back to the slow Discovery phase (Phase 1/Phase 2).

## Definition of Done
- A YAML config with just `url` and `monitor: auto`, `bypass: auto` can successfully extract jobs from Cloudflare-protected SPA sites (e.g. Ozon.tech, GetMatch).
- Unit tests verify the escalation order (Tier 0 -> Tier 1 -> Tier 3).
- DB tests verify that strategy caches are properly written and retrieved.
