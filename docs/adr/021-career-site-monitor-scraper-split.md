---
title: "ADR 021: Career Site Monitor/Scraper Split"
description: "ACCEPTED"
updated: 2026-07-24
---
# ADR 021: Career Site Monitor/Scraper Split

## Status
ACCEPTED

## Context
The current `CareerSiteSource` uses a declarative CSS-selector approach (`declarative.py`) to extract job listings and details. While effective for simple, static job boards, it struggles with:
- Modern Applicant Tracking Systems (ATS) that use internal APIs (Greenhouse, Lever, Ashby).
- Single Page Applications (SPAs) where content is JS-rendered or embedded in `__NEXT_DATA__`.
- Large boards where discovery via XML sitemaps is more reliable than crawling.

## Decision
We will adopt a two-stage architecture for career site ingestion, splitting the process into **Discovery** and **Extraction**.

### 1. Board Monitors (Discovery)
A `BoardMonitor` is responsible for identifying which jobs exist on a site.
- **Rich Monitors**: Can return both the job URL and its full content (title, description, etc.) in a single request (usually via an ATS API).
- **URL-only Monitors**: Only discover job URLs (e.g., from a sitemap or a list page).

### 2. Job Scrapers (Extraction)
A `JobScraper` is responsible for extracting structured content from a single job URL. This is only used when a monitor is URL-only or fails to provide rich data.

### 3. Orchestration
The `CareerSiteSource` will orchestrate these two components:
- **Auto-detection**: If `monitor="auto"`, the source will probe the URL to identify the best-matching monitor based on a cost-ranked registry.
- **Fallback Chain**: If the primary scraper fails or returns empty data, a chain of fallback scrapers (e.g., `json-ld` -> `embedded` -> `dom`) will be attempted.

### 4. Data Transfer Objects (DTOs)
New infra-layer DTOs (`DiscoveredPostingPayload`, `ScrapedPostingPayload`, `MonitorResult`) will handle data between monitors/scrapers and the domain-level `RawItem`.

## Consequences
- **Improved Reliability**: Better handling of ATS-specific formats.
- **Maintainability**: New boards can be added by creating a specific monitor/scraper without touching core logic.
- **Performance**: Rich monitors reduce the number of HTTP requests significantly.
- **Complexity**: Introduction of a registry and orchestration logic in the infrastructure layer.
- **Dependencies**: Adds optional dependencies like `jmespath` for advanced extraction.
