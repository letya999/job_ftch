# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Engineers and maintainers evaluating an open-source vacancy ingestion pipeline.
- AI engineers and AI developers who want to inspect the project through a concrete public example: the `ai_jobs` vacancy catalog.
- Agents and integrations consuming the same public catalog through MCP and HTTP surfaces.

## Product Purpose

`job_ftch` is an open-source, library-first asynchronous pipeline that collects vacancy signals from Telegram, career sites, RSS and APIs, normalizes them, removes duplicates and applies evidence-based relevance decisions. The public website explains the project briefly and exposes the `ai_jobs` tenant as one example of its work.

Success means a visitor can quickly understand the project, its architecture and extension model, then independently inspect the vacancies already published to the Telegram channel and the complete configured source registry.

## Positioning

The project is not a job board and not a scraper for one channel. It is a reusable hexagonal pipeline whose core stays independent from source and runtime integrations; registered adapters and application ports connect data sources, stores, sinks and external entrypoints.

## Operating Context

- Source signals enter through registered Telegram, career-site, RSS and API adapters.
- `SanitizeNode` is always the first processing stage.
- `EvidenceDecisionNode` owns the terminal runtime decision.
- The production-shaped runtime adapter is the Telegram bot.
- The MCP server is the agent-facing runtime adapter.
- The website reads the public `ai_jobs` source registry and publish ledger through a bounded read-only API.

## Capabilities and Constraints

- The site has three primary human routes: the project landing page, published vacancies and the complete source registry.
- Vacancy cards must match vacancies confirmed by the durable Telegram publish ledger.
- The source page must show every public-safe configured source and its type.
- Public data is read-only, allowlisted, rate-limited and cacheable.
- The existing Next.js, React, Bun and Tailwind stack remains.
- Human/agent projection, consent-gated analytics, legal routes, SEO and `llms.txt` remain available.
- No fabricated metrics, customers, testimonials or published vacancies.

## Brand Commitments

- Product name: `job_ftch`.
- The website is the project landing page, not a separate showcase product.
- Visual reference is binding: the structure and visual grammar should closely follow `https://kasetto.dev/` — dark technical surface, mono typography, thin grid, large outlined wordmark, violet and amber accents, rectangular controls and dense project footer.
- Copy is compact, factual and engineering-oriented.

## Evidence on Hand

- Project truth: `docs/vision.md`, `docs/architecture.md`, `docs/tech_stack.md`, `README.md`.
- Runtime data: public source registry and Telegram publish ledger for tenant `ai_jobs`.
- External visual reference: `https://kasetto.dev/` and the two user-provided screenshots.
- Repository: `https://github.com/letya999/job_ftch`.
- No public benchmark, star count or production adoption claim is currently confirmed.

## Product Principles

- Explain the reusable pipeline before presenting its vacancy example.
- Keep project truth and demonstration data visibly separate.
- Show real runtime state; empty published data is preferable to invented cards.
- Make architecture and extension points legible without turning the landing page into documentation.
- Preserve one data contract for the site, Telegram bot and agent-facing adapters.

