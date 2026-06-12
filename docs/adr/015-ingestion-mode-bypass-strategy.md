# 015 — IngestMode and BypassStrategy protocols

**Status**: ACCEPTED
**Date**: 2026-06-07

> Outdated note (2026-06-12): this ADR is preserved as the Phase 15 decision record.
> Current implementation drifted in three important ways:
> 1. `IngestMode` is currently driven via `run(source, on_item)` rather than the original cursor-shaped sketch below.
> 2. `BypassStrategy` evolved from HTTP-only fetch wrapping into a broader HTTP/browser boundary.
> 3. `RSSMode` remains roadmap work; the shipped built-ins are narrower than the list below.
>
> Current target architecture references:
> - [ADR-024](024-canonical-job-contract-and-matching-funnel.md)
> - [Architecture](../architecture.md)

## Context

Phase 15 introduces sources with different ingestion styles (polling, event-driven, RSS, inbound webhook, WebSocket) and sources protected by anti-scraping measures (IP blocks, CAPTCHAs, JS challenges, rate limits). Without a protocol boundary, each adapter hard-codes both its ingestion style and its bypass mechanism, making them impossible to mix, replace, or test independently.

## Decision

Two independent dimensions, each defined as a protocol in `application/contracts.py`:

### IngestMode

```python
class IngestMode(Protocol):
    async def fetch_items(self, source: Source, cursor: IncrementalCursor) -> AsyncIterator[RawItem]: ...
```

Implementations:
- `PollingMode(interval_seconds)` — periodic pull with `IncrementalCursor` watermark
- `EventListenerMode` — long-poll or webhook receiver
- `RSSMode` — parse Atom/RSS feed, cursor on `<updated>` or `<pubDate>`
- `WebhookMode(path)` — FastAPI route receiving inbound push
- `WebSocketMode(url)` — persistent WebSocket connection

### BypassStrategy

```python
class BypassStrategy(Protocol):
    async def fetch(self, url: str, client: httpx.AsyncClient) -> httpx.Response: ...
```

Implementations:
- `NoopBypass` — plain httpx (default, no overhead)
- `ProxyRotatorBypass(pool)` — rotate IPs from a proxy pool
- `StealthBrowserBypass` — Playwright with stealth fingerprinting
- `CaptchaSolverBypass(solver)` — solve image/reCAPTCHA via external service
- `ManagedScraperBypass(service_url)` — delegate to ScrapeOps / Apify

Both dimensions are orthogonal: a `TelegramChannelSource` uses `PollingMode + NoopBypass`; a protected career site uses `PollingMode + StealthBrowserBypass`. The combination is specified in `SourceSpec` and injected by the registry factory.

## Consequences

- (+) Ingestion style and bypass are independently testable with mocks.
- (+) New bypass strategies are plugins — no core edit needed.
- (+) `PollingMode` with `IncrementalCursor` is the universal fallback for any source.
- (-) `WebhookMode` requires an HTTP server to be running (FastAPI); complicates local dev.
- (-) `StealthBrowserBypass` adds playwright as an optional dep and significantly increases memory footprint per worker.
