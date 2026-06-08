# Phase 17-21 Completion Plan

Date: 2026-06-08
Branch: phase-17-21

## What This Plan Covers

Close all gaps in phases 17-21 discovered by audit:

| RM | Phase | What's missing |
|---|---|---|
| RM-089 | 17 | Scheduler does not enforce `rate_limit_min_interval_seconds` from SourceSpec |
| RM-092 | 18 | `VaultAuthProvider` stub |
| RM-093 | 18 | `EventListenerMode`, `WebhookMode`, `WebSocketMode` IngestMode implementations |
| RM-094/095 | 18 | `BypassStrategy` protocol implementations + `bypass` field in BaseSourceSpec |
| RM-096 | 18 | `config/sources.schema.json` + `config/sources.example.yaml` |
| RM-101 | 19 | `LeverAPISource` |
| RM-103 | 20 | `StealthBrowserBypass` stub (community-maintained) |
| RM-104 | 20 | `HardScraperSource` stub (community-maintained) |
| RM-105 | 20 | `ManagedScraperBypass` stub (community-maintained) |
| RM-108 | 21 | `WebhookSource` real implementation (aiohttp embedded server) |
| RM-109 | 21 | `WebSocketSource` real implementation (websockets reconnect loop) |

## Architecture Rules

- `domain/` — only pydantic + stdlib
- `application/` — only `domain/` + stdlib + pydantic
- `nodes/`, `sinks/` — only `domain/` + `application/`
- `infrastructure/` — everything above + external clients
- Heavy optional deps always in extras groups, never in core deps
- Community-maintained stubs: correct protocol + `NotImplementedError` with install instructions
- No AI attribution, English only, no secrets in code

---

## 1. domain/source_spec.py — Add `bypass` field to BaseSourceSpec

`BaseSourceSpec` already has `rate_limit_min_interval_seconds` and
`rate_limit_backoff_multiplier`. Add one more field:

```python
bypass: str | None = None  # registered bypass strategy name, e.g. "proxy_rotator"
bypass_config: dict[str, str] = Field(default_factory=dict)
```

This enables per-source bypass injection at runtime.

---

## 2. application/scheduler.py — Enforce rate limits (RM-089)

The Scheduler currently ignores `rate_limit_min_interval_seconds` from SourceSpec.
Fix: after each pipeline run for a source, enforce a minimum wait before the next run.

Modify `Scheduler.run_forever()` (or the per-source run loop):

```python
# After each pipeline run:
min_interval = source_spec.rate_limit_min_interval_seconds if source_spec else 0.0
if min_interval > 0:
    await asyncio.sleep(min_interval)
```

Also implement exponential backoff on source errors:
- Track consecutive errors per source in `_error_counts: dict[str, int]`
- On error: `sleep_time = min_interval * (backoff_multiplier ** error_count)`
- On success: reset error count for that source
- Cap backoff at `min_interval * 64` (6 doublings max)

---

## 3. infrastructure/auth/vault_auth.py (new) — VaultAuthProvider stub (RM-092)

```python
class VaultAuthProvider:
    """HashiCorp Vault / AWS Secrets Manager auth provider stub.
    
    Install: pip install hvac (HashiCorp Vault) or boto3 (AWS)
    """
    def __init__(self, vault_addr: str | None = None) -> None:
        raise NotImplementedError(
            "VaultAuthProvider is not yet implemented. "
            "Install hvac and implement infrastructure/auth/vault_auth.py. "
            "See docs/auth/vault.md for the implementation contract."
        )

    def resolve(self, source_id: str) -> dict[str, str]:
        raise NotImplementedError
```

---

## 4. infrastructure/ingest/ — IngestMode implementations (RM-093)

### 4a. infrastructure/ingest/event_listener.py (new)

`EventListenerMode` — infinite async generator, for sources like TelegramRealtimeSource
that push items via event handlers rather than being polled:

```python
class EventListenerMode:
    """IngestMode for event-driven sources (Telegram realtime, WebSocket)."""
    
    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
```

This is almost identical to `PollingMode` but semantically different — no interval sleep,
no re-invoke. The `fetch()` is an infinite generator driven by the source's event loop.

### 4b. infrastructure/ingest/webhook_mode.py (new)

`WebhookMode` — configures `WebhookSource` and runs it. This wraps `WebhookSource.fetch()`:

```python
class WebhookMode:
    """IngestMode for push-via-HTTP sources (WebhookSource)."""
    
    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
```

### 4c. infrastructure/ingest/websocket_mode.py (new)

`WebSocketMode` — same pattern, for `WebSocketSource`:

```python
class WebSocketMode:
    """IngestMode for persistent WebSocket sources."""

    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
```

---

## 5. infrastructure/bypass/ — BypassStrategy implementations (RM-094/095)

Create directory `infrastructure/bypass/` with the following files.

### 5a. infrastructure/bypass/__init__.py (new, empty)

### 5b. infrastructure/bypass/noop.py (new)

`NoopBypass` — default, passes client through unchanged. Register as `"noop"`.

```python
from application.registry import register_bypass

class NoopBypass:
    """Default bypass: no modification to the HTTP client."""
    def configure(self, client: Any) -> Any:
        return client

@register_bypass("noop")
def _create_noop() -> NoopBypass:
    return NoopBypass()
```

### 5c. infrastructure/bypass/proxy_rotator.py (new)

`ProxyRotatorBypass` — injects rotating HTTP proxies into httpx client.
Register as `"proxy_rotator"`.

```python
class ProxyRotatorBypass:
    """Rotate HTTP proxies on each request. Requires proxy_list in bypass_config."""
    
    def __init__(self, proxy_list: list[str]) -> None:
        self._proxies = proxy_list
        self._index = 0

    def configure(self, client: Any) -> Any:
        if not self._proxies:
            return client
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        # Return a new httpx.AsyncClient with proxy set
        import httpx
        if isinstance(client, httpx.AsyncClient):
            return httpx.AsyncClient(proxy=proxy, headers=client.headers)
        return client

@register_bypass("proxy_rotator")
def _create_proxy_rotator(bypass_config: dict[str, str] | None = None) -> ProxyRotatorBypass:
    config = bypass_config or {}
    proxy_list_raw = config.get("proxy_list", "")
    proxies = [p.strip() for p in proxy_list_raw.split(",") if p.strip()]
    return ProxyRotatorBypass(proxies)
```

### 5d. infrastructure/bypass/stealth_browser.py (new) — RM-103

`StealthBrowserBypass` stub. Requires `playwright-stealth` (community-maintained).

```python
try:
    import playwright_stealth  # type: ignore[import-untyped]
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

class StealthBrowserBypass:
    """Applies playwright-stealth patches to Playwright page context.
    
    Install: pip install playwright-stealth
    Community-maintained: see infrastructure/bypass/stealth_browser.py
    """
    
    def configure(self, client: Any) -> Any:
        if not _STEALTH_AVAILABLE:
            raise ImportError(
                "playwright-stealth is not installed. "
                "Run: pip install playwright-stealth"
            )
        # When a Playwright page is passed, apply stealth patches
        if hasattr(client, 'add_init_script'):
            playwright_stealth.stealth_sync(client)
        return client

@register_bypass("stealth_browser")
def _create_stealth() -> StealthBrowserBypass:
    return StealthBrowserBypass()
```

### 5e. infrastructure/bypass/captcha_solver.py (new)

`CaptchaSolverBypass` stub. Register as `"captcha_solver"`.

```python
class CaptchaSolverBypass:
    """Integrates Capsolver or 2captcha for CAPTCHA challenges.
    
    Community-maintained. Requires API key in bypass_config["api_key"].
    Providers: "capsolver", "2captcha"
    """
    def __init__(self, provider: str, api_key: str) -> None:
        self._provider = provider
        self._api_key = api_key

    def configure(self, client: Any) -> Any:
        raise NotImplementedError(
            f"CaptchaSolverBypass (provider={self._provider!r}) is not implemented. "
            "Implement in infrastructure/bypass/captcha_solver.py."
        )

@register_bypass("captcha_solver")
def _create_captcha_solver(bypass_config: dict[str, str] | None = None) -> CaptchaSolverBypass:
    config = bypass_config or {}
    return CaptchaSolverBypass(
        provider=config.get("provider", "capsolver"),
        api_key=config.get("api_key", ""),
    )
```

### 5f. infrastructure/bypass/behavior_sim.py (new)

`BehaviorSimBypass` stub. Register as `"behavior_sim"`.

```python
class BehaviorSimBypass:
    """Adds random delays and scroll events to simulate human interaction.
    
    Community-maintained. Used together with StealthBrowserBypass.
    """
    def __init__(self, min_delay: float = 0.5, max_delay: float = 2.0) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay

    def configure(self, client: Any) -> Any:
        raise NotImplementedError(
            "BehaviorSimBypass is not yet implemented. "
            "Implement in infrastructure/bypass/behavior_sim.py."
        )

@register_bypass("behavior_sim")
def _create_behavior_sim(bypass_config: dict[str, str] | None = None) -> BehaviorSimBypass:
    config = bypass_config or {}
    return BehaviorSimBypass(
        min_delay=float(config.get("min_delay", "0.5")),
        max_delay=float(config.get("max_delay", "2.0")),
    )
```

### 5g. infrastructure/bypass/managed.py (new) — RM-105

`ManagedScraperBypass` — delegates HTTP fetch to Scrapfly, ZenRows, or Browserless.
Register as `"managed_scraper"`.

```python
class ManagedScraperBypass:
    """Delegates HTTP fetch to a managed scraping API.
    
    Supported providers: "scrapfly", "zenrows", "browserless"
    Requires API key in bypass_config["api_key"] (resolved via AuthProvider).
    
    This is the recommended production path for CloudFlare-protected sites.
    """
    def __init__(self, api_url: str, api_key: str, provider: str = "scrapfly") -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._provider = provider

    def configure(self, client: Any) -> Any:
        """Returns a new httpx.AsyncClient configured to route via the managed API."""
        import httpx
        base_url = self._api_url
        headers: dict[str, str] = {}
        if self._provider == "scrapfly":
            headers["scp-sdk"] = "python"
            return httpx.AsyncClient(
                base_url=base_url,
                headers={**getattr(client, 'headers', {}), **headers},
                params={"key": self._api_key},
            )
        if self._provider in ("zenrows", "browserless"):
            return httpx.AsyncClient(
                base_url=base_url,
                headers={**getattr(client, 'headers', {}), "Authorization": f"Bearer {self._api_key}"},
            )
        return client

@register_bypass("managed_scraper")
def _create_managed(bypass_config: dict[str, str] | None = None) -> ManagedScraperBypass:
    config = bypass_config or {}
    return ManagedScraperBypass(
        api_url=config.get("api_url", ""),
        api_key=config.get("api_key", ""),
        provider=config.get("provider", "scrapfly"),
    )
```

---

## 6. infrastructure/sources/browser/hard_scraper.py (new) — RM-104

`HardScraperSource` stub. Community-maintained.

```python
class HardScraperSource:
    """Full browser scraping pipeline: sniffer -> scraper -> parser -> behavior sim.
    
    Community-maintained. Requires playwright + bypass infrastructure.
    See docs/sources/hard_scraper.md for implementation contract.
    """
    def __init__(self, spec: Any, auth: Any, bypass: Any | None = None) -> None:
        raise NotImplementedError(
            "HardScraperSource is not implemented. "
            "This is a community-maintained component. "
            "See docs/sources/hard_scraper.md for the implementation contract."
        )

    async def fetch(self) -> AsyncIterator[Any]:
        raise NotImplementedError
        yield
```

No `@register_source_v2` registration — this is a stub, users implement their own.

---

## 7. infrastructure/sources/api/lever.py (new) — RM-101

`LeverAPISource` — public Lever job board API.

Pattern follows `GreenhouseAPISource` exactly. Key differences:
- Endpoint: `https://api.lever.co/v0/postings/{company}?mode=json&limit=250`
- company slug is extracted from `spec.base_url` or `spec.params.get("company")`
- No auth required for public postings
- Response is a JSON array (not `{"data": [...]}`) — list at root level
- Field mapping:
  - `id` → `external_id`
  - `hostedUrl` → `url`
  - `text` → title
  - `descriptionPlain` or `description` → description text
  - `categories.location` → location
  - `categories.team` → metadata["team"]
  - `tags` → metadata["tags"]

```python
from application.registry import register_source_v2
from domain import RawItem, SourceKind
from infrastructure.sources.api.base import OfficialAPISource

class LeverAPISource(OfficialAPISource):
    """Lever public job board API. No auth required for public postings."""

    def _map_to_raw_item(self, data: dict[str, Any]) -> RawItem:
        text_parts = [
            data.get("text", ""),
            data.get("descriptionPlain") or data.get("description", ""),
        ]
        text = "\n\n".join(p for p in text_parts if p).strip()
        if not text:
            text = data.get("text", data.get("id", "unknown"))

        categories = data.get("categories", {})
        metadata: dict[str, Any] = {
            "team": categories.get("team"),
            "commitment": categories.get("commitment"),
            "tags": data.get("tags", []),
        }
        source_name = getattr(self.spec, "source_name", None) or "lever"
        return RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=str(data["id"]),
            url=data.get("hostedUrl"),
            text=text or "No description",
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    def _extract_items(self, response_data: Any) -> list[dict[str, Any]]:
        # Lever returns a JSON array at root, not {"data": [...]}
        if isinstance(response_data, list):
            return response_data
        return response_data.get("data", [])

    def _extract_cursor(self, response_data: Any) -> str | None:
        return None  # Lever uses limit param, no cursor pagination for public API

@register_source_v2("lever")
def _create_lever(spec: Any, auth: Any, store: Any | None = None) -> LeverAPISource:
    return LeverAPISource(spec, auth, store, source_kind=SourceKind.CAREER_SITE)
```

Also add `LeverAPISourceSpec` to `domain/source_spec.py`:
```python
class LeverSourceSpec(BaseSourceSpec):
    type: Literal["lever"] = "lever"
    company: str = Field(min_length=1, description="Lever company slug, e.g. 'acme'")
    source_name: str | None = None
```

Add `LeverSourceSpec` to the `SourceSpec` discriminated union.

---

## 8. infrastructure/sources/realtime/webhook.py — Real implementation (RM-108)

Replace the current stub with a working aiohttp embedded HTTP server.
Optional dep: `aiohttp` in `[realtime]` extras group.

```python
try:
    import aiohttp
    from aiohttp import web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

class WebhookSource:
    """Embedded aiohttp HTTP server. Yields RawItems from incoming POST requests."""
    
    def __init__(self, spec: WebhookSourceSpec, auth: AuthProvider) -> None:
        if not _AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp is required for WebhookSource. "
                "Run: pip install 'job_ftch[realtime]'"
            )
        self.spec = spec
        self.auth = auth
        self._queue: asyncio.Queue[RawItem] = asyncio.Queue()
        self._stop = asyncio.Event()

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        app = web.Application()
        app.router.add_post(self.spec.path, self._handle)

        runner = web.AppRunner(app)
        await runner.setup()
        host = getattr(self.spec, "host", "0.0.0.0")
        port = getattr(self.spec, "port", 8080)
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("webhook_source_listening", path=self.spec.path, port=port)

        try:
            while not self._stop.is_set():
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    yield item
                except asyncio.TimeoutError:
                    continue
        finally:
            await runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")
        
        source_name = self.spec.source_name or "webhook"
        text = _payload_to_text(payload)
        if not text:
            return web.Response(status=422, text="No text content found")
        
        item = RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=str(payload.get("id", id(payload))),
            url=payload.get("url"),
            text=text,
            metadata={"raw": payload},
        )
        await self._queue.put(item)
        return web.Response(status=200, text="OK")

    def stop(self) -> None:
        self._stop.set()

def _payload_to_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "body", "description", "message"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    return ""
```

Also add `host: str = "0.0.0.0"` and `port: int = 8080` fields to `WebhookSourceSpec`
in domain/source_spec.py.

---

## 9. infrastructure/sources/realtime/websocket.py — Real implementation (RM-109)

Replace stub with a working persistent WebSocket client using `websockets` library.
Optional dep: `websockets` in `[realtime]` extras group.

```python
try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False

class WebSocketSource:
    """Persistent WebSocket client with exponential backoff reconnection."""

    _MAX_BACKOFF = 300.0  # 5 minutes max backoff

    def __init__(self, spec: WebSocketSourceSpec, auth: AuthProvider) -> None:
        if not _WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "websockets is required for WebSocketSource. "
                "Run: pip install 'job_ftch[realtime]'"
            )
        self.spec = spec
        self.auth = auth
        self._stop = asyncio.Event()

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        backoff = 1.0
        source_name = self.spec.source_name or "websocket"
        
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.spec.url) as ws:
                    backoff = 1.0  # reset on successful connection
                    logger.info("websocket_source_connected", url=self.spec.url)
                    
                    async for message in ws:
                        if self._stop.is_set():
                            return
                        text = message if isinstance(message, str) else message.decode("utf-8", errors="replace")
                        if not text.strip():
                            continue
                        yield RawItem(
                            source_kind=SourceKind.CAREER_SITE,
                            source_name=source_name,
                            external_id=str(id(message)),
                            text=text.strip(),
                        )

            except ConnectionClosed:
                logger.warning("websocket_source_disconnected", url=self.spec.url, backoff=backoff)
            except Exception as exc:
                logger.error("websocket_source_error", url=self.spec.url, error=str(exc))
            
            if self._stop.is_set():
                return
            await asyncio.sleep(min(backoff, self._MAX_BACKOFF))
            backoff = min(backoff * 2, self._MAX_BACKOFF)

    def stop(self) -> None:
        self._stop.set()
```

---

## 10. pyproject.toml — Add [realtime] extras group

Find `[project.optional-dependencies]` and add:

```toml
realtime = [
    "aiohttp>=3.9.0",
    "websockets>=12.0",
]
```

Update `[all]` group to include `realtime`.

---

## 11. config/sources.schema.json (new) — RM-096

Generate JSON Schema from `SourceSpec` discriminated union. Add a script or add a
section to `app.py` that exports the schema:

Create `scripts/export_schema.py`:
```python
"""Export SourceSpec JSON Schema to config/sources.schema.json."""
import json
from pathlib import Path
from pydantic import TypeAdapter
from domain.source_spec import SourceSpec

if __name__ == "__main__":
    adapter = TypeAdapter(SourceSpec)
    schema = adapter.json_schema()
    out = Path("config/sources.schema.json")
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False))
    print(f"Schema written to {out}")
```

Then run it to generate the actual `config/sources.schema.json` file.

---

## 12. config/sources.example.yaml (new) — RM-096

```yaml
# sources.yaml — safe to commit, no credentials here
# See config/sources.schema.json for full schema reference

sources:
  # Telegram channels (polling)
  - type: telegram_channel
    entity: "@ai_jobs_ru"
    limit: 100
    auth_source_id: telegram
    interval_seconds: 3600

  # Telegram realtime (event listener mode)
  - type: telegram_realtime
    entity: "@getmatch"
    auth_source_id: telegram
    ingest_mode: event_listener

  # RSS feed (incremental)
  - type: rss_feed
    feed_url: "https://career.habr.com/vacancies/rss?q=machine+learning&type=1"
    incremental: true
    source_name: habr_ml

  # Official REST API (Greenhouse)
  - type: rest_api
    base_url: "https://api.greenhouse.io/v1/boards/example/"
    jobs_endpoint: "jobs"
    source_name: greenhouse_example

  # Lever public board
  - type: lever
    company: "acme-inc"
    source_name: lever_acme

  # HH.ru API
  - type: rest_api
    base_url: "https://api.hh.ru/"
    jobs_endpoint: "vacancies"
    params:
      text: "machine learning"
      area: "1"
    source_name: hh_ml

  # Career site (declarative HTML)
  - type: declarative_html
    url: "https://company.com/careers"
    parser_kind: auto
    source_name: company_careers

  # Browser source (requires playwright)
  - type: browser
    url: "https://protected-company.com/jobs"
    bypass: stealth_browser
    source_name: protected_jobs

  # Webhook (receives pushed job data)
  - type: webhook
    path: "/ingest/jobs"
    port: 8080
    source_name: pushed_jobs

  # WebSocket (streaming job data)
  - type: websocket
    url: "wss://realtime.jobboard.com/stream"
    source_name: ws_jobs
```

---

## 13. Tests to Add

### tests/test_phase18_bypass.py (new)

1. `test_noop_bypass_returns_client_unchanged` — `NoopBypass().configure(mock_client)` returns same object
2. `test_proxy_rotator_cycles_proxies` — two calls return different proxy-configured clients
3. `test_proxy_rotator_empty_list_is_noop` — empty proxy list returns client unchanged
4. `test_managed_scraper_bypass_scrapfly_sets_headers` — verify api_key in params
5. `test_bypass_registry_noop_registered` — `create_bypass("noop")` returns `NoopBypass`
6. `test_bypass_registry_proxy_rotator_registered` — `create_bypass("proxy_rotator", {...})` works
7. `test_stealth_browser_raises_without_dep` — mock `_STEALTH_AVAILABLE=False` → ImportError

### tests/test_phase19_lever.py (new)

1. `test_lever_api_source_maps_fields` — mock response with Lever JSON array structure, verify `RawItem` fields
2. `test_lever_source_spec_roundtrip` — `{"type": "lever", "company": "acme"}` → `LeverSourceSpec`
3. `test_lever_extract_items_handles_root_array` — response is `[{...}]` not `{"data": [{...}]}`

### tests/test_phase21_webhook.py (new)

1. `test_webhook_source_requires_aiohttp` — mock `_AIOHTTP_AVAILABLE=False` → ImportError with install message
2. `test_webhook_payload_to_text_extracts_text_field` — `_payload_to_text({"text": "hello"})` returns "hello"
3. `test_webhook_payload_to_text_fallback_fields` — tries "content", "body", "description" in order
4. `test_webhook_source_spec_has_host_port` — `WebhookSourceSpec(path="/x", port=9000)` validates OK

### tests/test_phase21_websocket.py (new)

1. `test_websocket_source_requires_websockets` — mock `_WEBSOCKETS_AVAILABLE=False` → ImportError
2. `test_websocket_source_spec_roundtrip` — `{"type": "websocket", "url": "wss://example.com"}` validates OK
3. `test_websocket_stop_event_terminates_fetch` — start WebSocketSource with mock ws, call stop(), verify terminates

### tests/test_phase17_rate_limit.py (new)

1. `test_scheduler_enforces_min_interval` — Source with `rate_limit_min_interval_seconds=0.5`, run twice, measure elapsed >= 0.5s
2. `test_scheduler_backoff_on_error` — simulate source error twice, verify sleep grows exponentially

---

## Quality Gates

```
uv run ruff format .
uv run ruff check .
uv run mypy .
uv run pytest tests/ -v --ignore=tests/e2e
rg "from infrastructure" domain/ application/ nodes/ sinks/  # must return empty
python scripts/export_schema.py  # generate config/sources.schema.json
```

---

## Constraints

1. All optional deps (aiohttp, websockets) guarded by `try/except ImportError` with clear install instructions
2. `infrastructure/bypass/` implementations: only stdlib + httpx (already a dep) for `NoopBypass` and `ProxyRotatorBypass`; heavier deps optional
3. `VaultAuthProvider` is a stub only — raises `NotImplementedError` on instantiation
4. `HardScraperSource` is a stub only — raises `NotImplementedError` on instantiation
5. `StealthBrowserBypass`, `CaptchaSolverBypass`, `BehaviorSimBypass` are stubs or minimal implementations
6. `ManagedScraperBypass.configure()` is a real implementation (uses httpx, already a dep)
7. `LeverAPISource` is a real implementation following the `OfficialAPISource` base pattern
8. `WebhookSource` and `WebSocketSource` are real implementations (not stubs)
9. No AI attribution, English only, no secrets in code, no co-authorship in commits
