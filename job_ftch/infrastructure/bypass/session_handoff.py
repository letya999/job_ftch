"""Stateful session handoff: curl_cffi <-> nodriver cookie/localStorage transfer.

When a lightweight curl_cffi request encounters a JS challenge (CAPTCHA/BLOCKED),
this module transparently degrades to nodriver, solves the challenge, harvests
cookies and localStorage, and reinjects them back into the curl_cffi session
so subsequent requests continue in high-performance HTTP mode.

Architecture:
- curl_cffi sends the initial request
- On CAPTCHA/BLOCKED failure, nodriver opens the same URL
- nodriver waits for the challenge to resolve (CF Turnstile auto-solves)
- Cookies are harvested via CDP Network.getAllCookies
- localStorage is harvested via JS evaluation
- Both are reinjected into the curl_cffi session's cookie jar
- The original request is retried on the now-warm curl_cffi session

The handoff state is cached per-origin with an asyncio.Lock to prevent
concurrent challenge solves for the same domain.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import BypassCapability, register_bypass, resolve_bypass
from job_ftch.infrastructure.bypass.failure_signal import FailureKind, HeuristicFailureSignal

logger = structlog.get_logger("job_ftch.bypass.handoff")

_HANDOFF_KINDS: frozenset[FailureKind] = frozenset(
    {FailureKind.CAPTCHA, FailureKind.CHALLENGE, FailureKind.BLOCKED}
)
_STATE_TTL_SECONDS: float = 1800.0
_MAX_ORIGINS: int = 128


@dataclass(slots=True)
class HandoffState:
    cookies: list[dict[str, Any]] = field(default_factory=list)
    local_storage: dict[str, str] = field(default_factory=dict)
    harvested_at: float = 0.0

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.harvested_at) > _STATE_TTL_SECONDS


class SessionHandoff:
    """curl_cffi first; on JS challenge, handoff to nodriver, harvest, re-inject."""

    def __init__(self, *, impersonate: str = "chrome") -> None:
        self._impersonate = impersonate
        self._signal = HeuristicFailureSignal()
        self._curl: Any = resolve_bypass("curl_stealth", {"impersonate": impersonate})
        self._nodriver: Any = resolve_bypass("nodriver")
        self._state: dict[str, HandoffState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Task[HandoffState]] = {}

    async def apply_http(self, client: Any) -> Any:
        curl_session = await self._curl.apply_http(client)

        outer = self

        class _HandoffAwareSession:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            async def get(self, url: str, **kw: Any) -> Any:
                resp = await self._inner.get(url, **kw)
                if await outer._needs_handoff(resp):
                    resp = await outer._do_handoff(
                        url,
                        self._inner,
                        "GET",
                        kw,
                        original_response=resp,
                    )
                return resp

            async def post(self, url: str, **kw: Any) -> Any:
                resp = await self._inner.post(url, **kw)
                if await outer._needs_handoff(resp):
                    replay_safe = bool(kw.pop("_replay_safe", False))
                    resp = await outer._do_handoff(
                        url,
                        self._inner,
                        "POST",
                        kw,
                        replay_safe=replay_safe,
                        original_response=resp,
                    )
                return resp

            async def __aenter__(self) -> _HandoffAwareSession:
                await self._inner.__aenter__()
                return self

            async def __aexit__(self, *a: object) -> None:
                await self._inner.__aexit__(*a)

        return _HandoffAwareSession(curl_session)

    async def _needs_handoff(self, resp: Any) -> bool:
        status = getattr(resp, "status_code", None) or getattr(resp, "status", None)
        body = b""
        if status in (403, 429, 503):
            try:
                body = await resp.acontent() if hasattr(resp, "acontent") else (resp.body or b"")
            except Exception:
                body = b""
        kind = self._signal.classify(status_code=status, body=body, error=None)
        return kind in _HANDOFF_KINDS

    async def _do_handoff(
        self,
        url: str,
        session: Any,
        method: str,
        kw: Any,
        *,
        replay_safe: bool = True,
        original_response: Any = None,
    ) -> Any:
        parsed = urlparse(url)
        origin = (parsed.hostname or "").lower()
        if parsed.port is not None:
            origin = f"{origin}:{parsed.port}"
        logger.info(
            "handoff_triggered",
            url_hash=hashlib.sha256(url.encode()).hexdigest()[:16],
            origin=origin,
            method=method,
        )

        lock = self._locks.setdefault(origin, asyncio.Lock())
        task: asyncio.Task[HandoffState] | None = None
        async with lock:
            state = self._state.get(origin)
            if state is None or state.expired:
                inflight = getattr(self, "_inflight", None)
                if inflight is None:
                    inflight = self._inflight = {}
                task = inflight.get(origin)
                if task is None:
                    task = asyncio.create_task(
                        self._harvest_state(url),
                        name=f"session-handoff:{origin}",
                    )
                    inflight[origin] = task

        # Provider/browser waits happen outside the per-origin state lock.
        if task is not None:
            try:
                state = await task
            finally:
                async with lock:
                    if getattr(self, "_inflight", {}).get(origin) is task:
                        self._inflight.pop(origin, None)
            async with lock:
                self._state[origin] = state
                self._prune_state()

        assert state is not None
        await self._reinject(session, state)

        if state.local_storage:
            logger.info(
                "handoff_requires_browser_continuation",
                origin=origin,
                storage_keys=len(state.local_storage),
            )
            return original_response

        if method == "GET":
            return await session.get(url, **kw)
        if not replay_safe:
            return original_response
        return await session.post(url, **kw)

    def _prune_state(self) -> None:
        expired = [origin for origin, state in self._state.items() if state.expired]
        for origin in expired:
            self._state.pop(origin, None)
            self._locks.pop(origin, None)
            getattr(self, "_inflight", {}).pop(origin, None)
        while len(self._state) > _MAX_ORIGINS:
            newest = min(self._state, key=lambda key: self._state[key].harvested_at)
            self._state.pop(newest, None)
            self._locks.pop(newest, None)
            getattr(self, "_inflight", {}).pop(newest, None)

    async def _harvest_state(self, target_url: str) -> HandoffState:
        cfg = {
            "headless": True,
            "warmup_url": target_url,
            "timeout": 30000,
            "viewport": {"width": 1440, "height": 900},
        }
        state = HandoffState(harvested_at=time.monotonic())

        try:
            async with self._nodriver.open_page(cfg) as page:
                try:
                    cdp = None
                    try:
                        from nodriver import cdp as _cdp

                        cdp = _cdp
                    except ImportError:
                        pass

                    if cdp is not None and hasattr(page, "_tab"):
                        cookies = await page._tab.send(cdp.network.get_all_cookies())
                        state.cookies = [_cookie_to_dict(c) for c in cookies]
                    elif hasattr(page, "context") and hasattr(page.context, "cookies"):
                        raw = await page.context.cookies()
                        state.cookies = [dict(c) for c in raw]
                except Exception as exc:
                    logger.warning("handoff_cookie_harvest_failed", error=str(exc))

                try:
                    storage_json = await page.evaluate(
                        "(()=>{const out={}; for(let i=0;i<localStorage.length;i++){"
                        "const k=localStorage.key(i); out[k]=localStorage.getItem(k);} return out;})()"
                    )
                    if isinstance(storage_json, dict):
                        state.local_storage = {str(k): str(v) for k, v in storage_json.items()}
                except Exception as exc:
                    logger.debug("handoff_localstorage_harvest_failed", error=str(exc))
        except Exception as exc:
            logger.warning("handoff_browser_open_failed", error=str(exc))

        origin = (urlparse(target_url).hostname or "").lower()
        logger.info(
            "handoff_state_harvested",
            origin=origin,
            cookies=len(state.cookies),
            storage_keys=len(state.local_storage),
        )
        return state

    async def _reinject(self, session: Any, state: HandoffState) -> None:
        inner = getattr(session, "_inner", session)
        sessions = list(getattr(inner, "_sessions", {}).values())
        legacy = getattr(inner, "_sess", None) or getattr(inner, "_client", None)
        if legacy is not None:
            sessions.append(legacy)
        for active_session in sessions:
            jar = getattr(active_session, "cookies", None)
            if jar is None:
                continue
            for c in state.cookies:
                try:
                    jar.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )
                except Exception:
                    continue

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return cast("dict[str, Any]", self._curl.apply_browser_args(kwargs))

    async def apply_page(self, page: Any) -> None:
        await self._curl.apply_page(page)


def _cookie_to_dict(c: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in (
        "name",
        "value",
        "domain",
        "path",
        "expires",
        "size",
        "http_only",
        "secure",
        "same_site",
    ):
        v = getattr(c, attr, None)
        if v is not None:
            key = "httpOnly" if attr == "http_only" else attr
            out[key] = v
    return out


def _create_handoff(cfg: dict[str, Any] | None = None) -> SessionHandoff:
    cfg = cfg or {}
    return SessionHandoff(impersonate=cfg.get("impersonate", "chrome"))


with contextlib.suppress(Exception):
    register_bypass(
        "curl_with_handoff",
        capability=BypassCapability(
            cost=25,
            transport="curl_handoff",
            owns_session=True,
            challenge_actions=frozenset({"session_handoff"}),
            legal_gate="adr_073",
            min_remaining_seconds=10.0,
        ),
    )(_create_handoff)
