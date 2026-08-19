"""Bounded live listing probe for operator adapters.

Opens one ephemeral browser page through ``open_page`` / ``navigate``. Does not
ingest into the pipeline, persist profiles, or return cookies.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from job_ftch.application.registry import resolve_bypass
from job_ftch.config import Settings, get_settings
from job_ftch.infrastructure.network.ssrf_guard import check_ssrf
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page

log = structlog.get_logger()

LISTING_PROBE_DEADLINE_SECONDS = 45.0
LISTING_NAV_TIMEOUT_MS = 20_000
LISTING_MAX_ITEMS_CAP = 20
LISTING_CARD_WAIT_MS = 5_000
LISTING_SETTLE_SECONDS = 2.0
_DETAIL_PATH = re.compile(
    r"/(?:jobs?|vacancies|vacancy|careers?|positions?|openings?)/\d[\w-]*",
    re.IGNORECASE,
)
_HREF_ATTR = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

ENGINE_ALIASES: dict[str, str] = {
    "auto": "patchright_browser",
    "patchright": "patchright_browser",
    "patchright_browser": "patchright_browser",
    "stealth_browser": "stealth_browser",
    "nodriver": "nodriver",
    "camoufox": "camoufox",
    "cloak": "cloak",
}

_EXTRACT_LINKS = """
(maxItems) => {
  const detailRe = /\\/(?:jobs?|vacancies|vacancy|careers?|positions?|openings?)\\/\\d/i;
  const seen = new Set();
  const collected = [];
  const origin = location.origin;
  for (const anchor of document.querySelectorAll("a[href]")) {
    const href = String(anchor.href || "");
    if (!href.startsWith("http")) continue;
    let parsed;
    try {
      parsed = new URL(href);
    } catch (error) {
      continue;
    }
    if (parsed.origin !== origin) continue;
    if (!detailRe.test(parsed.pathname)) continue;
    const url = parsed.origin + parsed.pathname;
    if (seen.has(url)) continue;
    seen.add(url);
    const title = String(anchor.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 120);
    collected.push({ url, title });
    if (collected.length >= maxItems) break;
  }
  return collected;
}
"""


def resolve_probe_engine(engine: str) -> str:
    normalized = (engine or "auto").strip().lower()
    return ENGINE_ALIASES.get(normalized, normalized)


def _result(
    *,
    status: str,
    ok: bool = False,
    executed: bool = False,
    error: str | None = None,
    notes: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "executed": executed,
        "error": error,
        "notes": notes or [],
    }
    payload.update(extra)
    return payload


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_detail_url(url: str, *, origin: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False
    if origin and f"{parsed.scheme}://{parsed.netloc}" != origin:
        return False
    return bool(_DETAIL_PATH.search(parsed.path or ""))


def _canonical_detail_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _public_items(raw: object, *, origin: str, max_items: int) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not _is_detail_url(url, origin=origin):
            continue
        canonical = _canonical_detail_url(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        title = str(entry.get("title") or "").strip()[:120]
        items.append({"url": canonical, "title": title})
        if len(items) >= max_items:
            break
    return items


def _items_from_html(html: str, *, page_url: str, max_items: int) -> list[dict[str, str]]:
    origin = _origin(page_url)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _HREF_ATTR.finditer(html or ""):
        absolute = urljoin(page_url, match.group(1)).split("#")[0]
        if not _is_detail_url(absolute, origin=origin):
            continue
        canonical = _canonical_detail_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        items.append({"url": canonical, "title": ""})
        if len(items) >= max_items:
            break
    return items


class _ChallengeSink:
    """Wrap a bypass so listing probes can record challenge detections."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.observed_challenge_type: str | None = getattr(inner, "observed_challenge_type", None)

    def set_observed_challenge_type(self, challenge_type: str | None) -> None:
        if challenge_type:
            self.observed_challenge_type = str(challenge_type)
        setter = getattr(self._inner, "set_observed_challenge_type", None)
        if callable(setter) and setter is not self.set_observed_challenge_type:
            setter(challenge_type)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _attach_challenge_sink(strategy: Any) -> Any:
    if isinstance(strategy, _ChallengeSink):
        return strategy
    return _ChallengeSink(strategy)


def _observed_challenge(strategy: Any) -> str | None:
    value = getattr(strategy, "observed_challenge_type", None)
    text = str(value or "").strip()
    return text or None


async def _wait_for_cards(page: Any) -> None:
    wait_for = getattr(page, "wait_for_selector", None)
    if callable(wait_for):
        try:
            await wait_for(
                'a[href*="/vacancies/"], a[href*="/jobs/"], a[href*="/job/"]',
                timeout=LISTING_CARD_WAIT_MS,
            )
            return
        except Exception:
            pass
    await asyncio.sleep(LISTING_SETTLE_SECONDS)


async def _extract_items(page: Any, *, page_url: str, max_items: int) -> list[dict[str, str]]:
    origin = _origin(page_url)
    evaluate = getattr(page, "evaluate", None)
    raw: object = []
    if callable(evaluate):
        try:
            raw = await evaluate(_EXTRACT_LINKS, max_items)
        except Exception as exc:
            log.info("browser_probe.extract_failed", error=type(exc).__name__)
    items = _public_items(raw, origin=origin, max_items=max_items)
    if items:
        return items
    content_fn = getattr(page, "content", None)
    if not callable(content_fn):
        return []
    try:
        html = content_fn()
        if hasattr(html, "__await__"):
            html = await html
    except Exception:
        return []
    return _items_from_html(str(html or ""), page_url=page_url, max_items=max_items)


async def _page_title(page: Any) -> str | None:
    title_fn = getattr(page, "title", None)
    if not callable(title_fn):
        return None
    try:
        result = title_fn()
        if hasattr(result, "__await__"):
            result = await result
        text = str(result or "").strip()
    except Exception:
        return None
    return text[:200] or None


DETAIL_TEXT_CAP = 2_000


async def _page_text(page: Any, *, limit: int = DETAIL_TEXT_CAP) -> str:
    evaluate = getattr(page, "evaluate", None)
    if callable(evaluate):
        try:
            raw = await evaluate(
                "(limit) => String(document.body && document.body.innerText || '').slice(0, limit)",
                limit,
            )
            return str(raw or "")[:limit]
        except Exception:
            pass
    content_fn = getattr(page, "content", None)
    if not callable(content_fn):
        return ""
    try:
        html = content_fn()
        if hasattr(html, "__await__"):
            html = await html
    except Exception:
        return ""
    return str(html or "")[:limit]


async def _page_heading(page: Any) -> str | None:
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return None
    try:
        raw = await evaluate(
            "() => String(document.querySelector('h1')?.textContent || '').replace(/\\s+/g, ' ').trim()"
        )
    except Exception:
        return None
    text = str(raw or "").strip()
    return text[:200] or None


async def _page_html(page: Any) -> str:
    content_fn = getattr(page, "content", None)
    if not callable(content_fn):
        return ""
    try:
        html = content_fn()
        if hasattr(html, "__await__"):
            html = await html
    except Exception:
        return ""
    return str(html or "")


def _classify_html(html: str) -> str | None:
    from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge

    detection = classify_challenge(surface="operator_probe", body=html)
    if detection.challenge_type:
        return str(detection.challenge_type)
    if detection.detected:
        return str(detection.kind.value)
    return None


def _public_solve(result: Any) -> dict[str, Any]:
    failure = getattr(result, "failure_reason", None)
    kind = getattr(result, "result_kind", None)
    return {
        "solved": bool(getattr(result, "solved", False)),
        "method": getattr(result, "method", None),
        "error": getattr(result, "error", None),
        "failure_reason": getattr(failure, "value", failure),
        "challenge_type": getattr(result, "challenge_type", None),
        "result_kind": getattr(kind, "value", kind),
        "elapsed_seconds": getattr(result, "elapsed_seconds", None),
    }


def _make_solver(solve: str, bypass_config: dict[str, Any] | None) -> Any | None:
    normalized = (solve or "none").strip().lower()
    if normalized in {"", "none"}:
        return None
    from job_ftch.infrastructure.bypass.captcha_solver import (
        CaptchaSolverBypass,
        _create_captcha_solver,
    )

    if normalized in {"browser_wait", "auto"}:
        return CaptchaSolverBypass(provider="browser_wait", api_key="", wait_seconds=8.0)
    if normalized == "provider":
        return _create_captcha_solver(bypass_config)
    return None


async def _run_live_probe(
    *,
    url: str,
    engine: str,
    headed: bool,
    bypass_config: dict[str, Any] | None,
    deadline_seconds: float,
    notes: list[str],
    handler: Any,
) -> dict[str, Any]:
    target = url.strip()
    resolved_engine = resolve_probe_engine(engine)
    if headed:
        notes = [*notes, "headed=true is operator-visible and still uses an ephemeral profile"]
    try:
        strategy = resolve_bypass(resolved_engine, bypass_config)
    except ValueError:
        return _result(
            status="unavailable",
            error="engine_unavailable",
            notes=[
                *notes,
                f"bypass engine {resolved_engine!r} is not registered",
                "install the matching extra (browser/stealth/nodriver/camoufox) and retry",
            ],
            engine=resolved_engine,
            requested_url=target,
        )

    challenge_sink = _attach_challenge_sink(strategy)
    config: dict[str, Any] = {
        "url": target,
        "headless": not headed,
        "timeout": LISTING_NAV_TIMEOUT_MS,
        "wait": "domcontentloaded",
        "persistent_context": False,
        "_bypass_strategy": challenge_sink,
    }

    async def _run() -> dict[str, Any]:
        await check_ssrf(target)
        async with open_page(config, bypass_strategy=strategy) as page:
            await navigate(page, target, config)
            payload = await handler(page, challenge_sink, target, resolved_engine, notes)
            if isinstance(payload, dict):
                return payload
            return _result(
                status="error",
                error="invalid_probe_payload",
                engine=resolved_engine,
                requested_url=target,
            )

    try:
        return await asyncio.wait_for(_run(), timeout=deadline_seconds)
    except TimeoutError:
        return _result(
            status="timeout",
            error="listing_probe_deadline",
            notes=[*notes, f"overall deadline {deadline_seconds:.0f}s"],
            engine=resolved_engine,
            requested_url=target,
        )
    except httpx.LocalProtocolError as exc:
        return _result(
            status="error",
            error="ssrf_blocked",
            notes=[*notes, str(exc)],
            engine=resolved_engine,
            requested_url=target,
        )
    except (ImportError, RuntimeError) as exc:
        message = str(exc)
        if "patchright" in message.lower() or "playwright" in message.lower():
            return _result(
                status="unavailable",
                error="browser_runtime_missing",
                notes=[
                    *notes,
                    message[:240],
                    "uv sync --extra browser && uv run patchright install chromium",
                ],
                engine=resolved_engine,
                requested_url=target,
            )
        return _result(
            status="error",
            error=type(exc).__name__,
            notes=[*notes, message[:240]],
            engine=resolved_engine,
            requested_url=target,
        )
    except Exception as exc:
        log.warning("browser_probe.listing_failed", error=type(exc).__name__)
        return _result(
            status="error",
            error=type(exc).__name__,
            notes=[*notes, str(exc)[:240]],
            engine=resolved_engine,
            requested_url=target,
        )


async def probe_listing(
    *,
    url: str,
    engine: str = "auto",
    headed: bool = False,
    max_items: int = 5,
    bypass_config: dict[str, Any] | None = None,
    settings: Settings | None = None,
    deadline_seconds: float = LISTING_PROBE_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Open one listing URL and return bounded public link previews."""
    del settings
    limit = max(1, min(int(max_items), LISTING_MAX_ITEMS_CAP))
    notes = [
        "listing probe opens one ephemeral headless page",
        "does not ingest, persist cookies, or keep a browser session",
    ]

    async def _handler(
        page: Any,
        challenge_sink: Any,
        target: str,
        resolved_engine: str,
        probe_notes: list[str],
    ) -> dict[str, Any]:
        await _wait_for_cards(page)
        await asyncio.sleep(0)
        final_url = str(getattr(page, "url", "") or target)
        items = await _extract_items(page, page_url=final_url, max_items=limit)
        challenge = _observed_challenge(challenge_sink) or _classify_html(await _page_html(page))
        result_notes = list(probe_notes)
        if challenge:
            result_notes.append(f"challenge observed: {challenge}")
            result_notes.append("retry with a stronger engine or run_source adaptive ingest")
        if not items:
            result_notes.append(
                "no vacancy-like URLs (need /jobs|/vacancies/<id>); nav chrome is ignored"
            )
        if challenge and not items:
            status, ok, error = "challenge", False, "challenge_detected"
        elif not items:
            status, ok, error = "empty", True, None
        else:
            status, ok, error = "ok", True, None
        return _result(
            status=status,
            ok=ok,
            executed=True,
            error=error,
            notes=result_notes,
            engine=resolved_engine,
            requested_url=target,
            final_url=final_url,
            page_title=await _page_title(page),
            challenge=challenge,
            item_count=len(items),
            items=items,
        )

    return await _run_live_probe(
        url=url,
        engine=engine,
        headed=headed,
        bypass_config=bypass_config,
        deadline_seconds=deadline_seconds,
        notes=notes,
        handler=_handler,
    )


async def probe_detail(
    *,
    url: str,
    engine: str = "auto",
    headed: bool = False,
    max_items: int = 5,
    bypass_config: dict[str, Any] | None = None,
    settings: Settings | None = None,
    deadline_seconds: float = LISTING_PROBE_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Open one detail URL and return a bounded public text preview."""
    del settings
    limit = max(1, min(int(max_items), LISTING_MAX_ITEMS_CAP))
    notes = [
        "detail probe opens one ephemeral page",
        "returns title/heading/text preview; does not ingest",
    ]

    async def _handler(
        page: Any,
        challenge_sink: Any,
        target: str,
        resolved_engine: str,
        probe_notes: list[str],
    ) -> dict[str, Any]:
        await asyncio.sleep(LISTING_SETTLE_SECONDS)
        final_url = str(getattr(page, "url", "") or target)
        heading = await _page_heading(page)
        text_preview = await _page_text(page)
        items = await _extract_items(page, page_url=final_url, max_items=limit)
        challenge = _observed_challenge(challenge_sink) or _classify_html(await _page_html(page))
        result_notes = list(probe_notes)
        if challenge:
            result_notes.append(f"challenge observed: {challenge}")
        if challenge and not text_preview and not heading:
            status, ok, error = "challenge", False, "challenge_detected"
        elif not heading and not text_preview:
            status, ok, error = "empty", True, None
        else:
            status, ok, error = "ok", True, None
        return _result(
            status=status,
            ok=ok,
            executed=True,
            error=error,
            notes=result_notes,
            engine=resolved_engine,
            requested_url=target,
            final_url=final_url,
            page_title=await _page_title(page),
            heading=heading,
            text_preview=text_preview,
            challenge=challenge,
            item_count=len(items),
            items=items,
        )

    return await _run_live_probe(
        url=url,
        engine=engine,
        headed=headed,
        bypass_config=bypass_config,
        deadline_seconds=deadline_seconds,
        notes=notes,
        handler=_handler,
    )


async def probe_challenge(
    *,
    url: str,
    engine: str = "auto",
    headed: bool = False,
    max_items: int = 5,
    bypass_config: dict[str, Any] | None = None,
    settings: Settings | None = None,
    deadline_seconds: float = LISTING_PROBE_DEADLINE_SECONDS,
    solve: str = "none",
) -> dict[str, Any]:
    """Detect a challenge page; optionally wait/solve under captcha gates."""
    del settings, max_items
    normalized_solve = (solve or "none").strip().lower() or "none"
    notes = [
        "challenge probe opens one ephemeral page",
        "does not return cookies, tokens, or provider secrets",
    ]
    if normalized_solve not in {"none", "browser_wait", "auto", "provider"}:
        return _result(
            status="unsupported",
            error="unsupported_solve",
            notes=[*notes, "solve must be none|browser_wait|auto|provider"],
            engine=resolve_probe_engine(engine),
            requested_url=url.strip(),
            solve=normalized_solve,
        )
    solver = _make_solver(normalized_solve, bypass_config)

    async def _handler(
        page: Any,
        challenge_sink: Any,
        target: str,
        resolved_engine: str,
        probe_notes: list[str],
    ) -> dict[str, Any]:
        await asyncio.sleep(LISTING_SETTLE_SECONDS)
        final_url = str(getattr(page, "url", "") or target)
        html = await _page_html(page)
        challenge = _observed_challenge(challenge_sink) or _classify_html(html)
        solve_payload: dict[str, Any] | None = None
        result_notes = list(probe_notes)
        if solver is not None:
            result = await solver.solve(page, challenge_type=challenge or "unknown", url=final_url)
            solve_payload = _public_solve(result)
            html = await _page_html(page)
            challenge = _observed_challenge(challenge_sink) or _classify_html(html) or challenge
            if result.solved:
                result_notes.append(f"challenge solve via {result.method}")
            elif result.error:
                result_notes.append(str(result.error)[:200])
        if challenge and not (solve_payload and solve_payload.get("solved")):
            status, ok, error = "challenge", False, "challenge_detected"
        elif solve_payload and solve_payload.get("solved"):
            status, ok, error = "ok", True, None
        else:
            status, ok, error = "ok", True, None
            result_notes.append("no challenge detected")
        return _result(
            status=status,
            ok=ok,
            executed=True,
            error=error,
            notes=result_notes,
            engine=resolved_engine,
            requested_url=target,
            final_url=final_url,
            page_title=await _page_title(page),
            challenge=challenge,
            solve=normalized_solve,
            captcha=solve_payload,
        )

    return await _run_live_probe(
        url=url,
        engine=engine,
        headed=headed,
        bypass_config=bypass_config,
        deadline_seconds=deadline_seconds,
        notes=notes,
        handler=_handler,
    )


_SAFE_FINGERPRINT_KEYS = frozenset({"render", "challenge", "board_gone", "spa", "jsonld"})


async def probe_fingerprint(
    *,
    url: str,
    engine: str = "auto",
    headed: bool = False,
    max_items: int = 5,
    bypass_config: dict[str, Any] | None = None,
    settings: Settings | None = None,
    deadline_seconds: float = LISTING_PROBE_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """HTTP site-class probe plus an optional same-page UA snapshot. No cookies."""
    del max_items, settings
    notes = [
        "fingerprint probe uses HTTP site classification",
        "does not persist cookies, profiles, or challenge tokens",
    ]
    target = url.strip()
    resolved_engine = resolve_probe_engine(engine)
    try:
        await check_ssrf(target)
    except Exception as exc:  # noqa: BLE001 - operator probe must not crash MCP
        error = "ssrf_blocked" if isinstance(exc, httpx.LocalProtocolError) else type(exc).__name__
        return _result(
            status="error",
            error=error,
            notes=[*notes, str(exc)[:240]],
            engine=resolved_engine,
            requested_url=target,
        )
    from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint as http_fingerprint

    try:
        profile = await http_fingerprint(target)
    except Exception as exc:  # noqa: BLE001
        return _result(
            status="error",
            executed=True,
            error=type(exc).__name__,
            notes=[*notes, str(exc)[:240]],
            engine=resolved_engine,
            requested_url=target,
        )
    detected = {
        key: value
        for key, value in dict(profile.detected_config or {}).items()
        if key in _SAFE_FINGERPRINT_KEYS
    }
    payload = {
        "site_class": str(profile.site_class),
        "recommended_monitors": list(profile.recommended_monitors or [])[:8],
        "canonical_url": profile.canonical_url,
        "detected": detected,
    }
    user_agent = None
    if headed:
        notes.append("headed fingerprint still does not keep a session")

        async def _handler(
            page: Any,
            challenge_sink: Any,
            requested: str,
            engine_name: str,
            probe_notes: list[str],
        ) -> dict[str, Any]:
            del challenge_sink, requested, engine_name
            ua = None
            evaluate = getattr(page, "evaluate", None)
            if callable(evaluate):
                try:
                    raw = evaluate("() => navigator.userAgent")
                    if hasattr(raw, "__await__"):
                        raw = await raw
                    ua = str(raw or "")[:180] or None
                except Exception:
                    ua = None
            return _result(
                status="ok",
                ok=True,
                executed=True,
                notes=probe_notes,
                engine=resolved_engine,
                requested_url=target,
                final_url=str(getattr(page, "url", "") or target),
                page_title=await _page_title(page),
                fingerprint=payload,
                user_agent=ua,
            )

        try:
            live = await _run_live_probe(
                url=target,
                engine=engine,
                headed=True,
                bypass_config=bypass_config,
                deadline_seconds=min(deadline_seconds, 20.0),
                notes=notes,
                handler=_handler,
            )
            if live.get("executed"):
                live.setdefault("fingerprint", payload)
                return live
        except Exception:
            pass
    challenge = bool(detected.get("challenge"))
    return _result(
        status="challenge" if challenge else "ok",
        ok=not challenge,
        executed=True,
        error="challenge_detected" if challenge else None,
        notes=notes,
        engine=resolved_engine,
        requested_url=target,
        fingerprint=payload,
        user_agent=user_agent,
    )


async def probe_custom_safe(
    *,
    url: str,
    engine: str = "auto",
    headed: bool = False,
    max_items: int = 5,
    bypass_config: dict[str, Any] | None = None,
    settings: Settings | None = None,
    deadline_seconds: float = LISTING_PROBE_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """One page, no clicks/forms/cookies: title, challenge, bounded link preview."""
    payload = await probe_listing(
        url=url,
        engine=engine,
        headed=headed,
        max_items=min(int(max_items), 3),
        bypass_config=bypass_config,
        settings=settings,
        deadline_seconds=deadline_seconds,
    )
    notes = list(payload.get("notes") or [])
    notes.append("custom_safe: no clicks, no forms, no cookie values")
    payload["notes"] = notes
    payload["probe"] = "custom_safe"
    return payload


class LiveBrowserSessionProbe:
    """Application-port adapter over listing/detail/challenge/fingerprint/custom_safe probes."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def probe_listing(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await probe_listing(
            url=url,
            engine=engine,
            headed=headed,
            max_items=max_items,
            bypass_config=bypass_config,
            settings=self._settings,
        )

    async def probe_detail(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await probe_detail(
            url=url,
            engine=engine,
            headed=headed,
            max_items=max_items,
            bypass_config=bypass_config,
            settings=self._settings,
        )

    async def probe_challenge(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
        solve: str = "none",
    ) -> dict[str, Any]:
        return await probe_challenge(
            url=url,
            engine=engine,
            headed=headed,
            max_items=max_items,
            bypass_config=bypass_config,
            settings=self._settings,
            solve=solve,
        )

    async def probe(
        self,
        *,
        url: str,
        engine: str,
        probe: str = "listing",
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
        solve: str = "none",
    ) -> dict[str, Any]:
        kind = (probe or "listing").strip().lower()
        if kind == "detail":
            return await self.probe_detail(
                url=url,
                engine=engine,
                headed=headed,
                max_items=max_items,
                bypass_config=bypass_config,
            )
        if kind == "challenge":
            return await self.probe_challenge(
                url=url,
                engine=engine,
                headed=headed,
                max_items=max_items,
                bypass_config=bypass_config,
                solve=solve,
            )
        if kind == "fingerprint":
            return await probe_fingerprint(
                url=url,
                engine=engine,
                headed=headed,
                max_items=max_items,
                bypass_config=bypass_config,
                settings=self._settings,
            )
        if kind == "custom_safe":
            return await probe_custom_safe(
                url=url,
                engine=engine,
                headed=headed,
                max_items=max_items,
                bypass_config=bypass_config,
                settings=self._settings,
            )
        return await self.probe_listing(
            url=url,
            engine=engine,
            headed=headed,
            max_items=max_items,
            bypass_config=bypass_config,
        )
