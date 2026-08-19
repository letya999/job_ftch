"""In-process operator browser sessions.

Holds at most a few open_page contexts with a hard TTL. Persistent/domain
profiles live under browser_profile_root/operator/<key>. Does not return
cookie values, filesystem paths, or import browser clients into adapters.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from job_ftch.application.registry import resolve_bypass
from job_ftch.infrastructure.browser_probe import (
    _attach_challenge_sink,
    _classify_html,
    _observed_challenge,
    _page_html,
    _page_text,
    _page_title,
    _public_solve,
    resolve_probe_engine,
)
from job_ftch.infrastructure.network.ssrf_guard import check_ssrf
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page

log = structlog.get_logger()

SESSION_TTL_SECONDS = 180.0
HEADED_SESSION_TTL_SECONDS = 900.0
OPEN_DEADLINE_SECONDS = 45.0
COMMAND_DEADLINE_SECONDS = 30.0
MAX_SESSIONS = 2
HTML_CAP = 8_000
NAV_TIMEOUT_MS = 20_000
ARTIFACT_TYPES = frozenset({"text", "html", "cookies_summary", "screenshot", "trace"})
PROFILES = frozenset({"ephemeral", "persistent", "domain"})


def _session_result(
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


def _same_origin(left: str, right: str) -> bool:
    a = urlparse(left)
    b = urlparse(right)
    return bool(a.scheme and a.netloc and (a.scheme, a.netloc) == (b.scheme, b.netloc))


async def _cookie_names(page: Any) -> list[str]:
    context = getattr(page, "context", None)
    cookies_fn = getattr(context, "cookies", None) if context is not None else None
    if not callable(cookies_fn):
        cookies_fn = getattr(page, "cookies", None)
    if not callable(cookies_fn):
        return []
    try:
        raw = cookies_fn()
        if hasattr(raw, "__await__"):
            raw = await raw
    except Exception:
        return []
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
    return names


@dataclass
class _Command:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    future: asyncio.Future[dict[str, Any]] | None = None


class _LiveSession:
    def __init__(
        self,
        *,
        tenant_id: str,
        url: str,
        engine: str,
        headed: bool,
        bypass_config: dict[str, Any] | None,
        manual_challenge: bool,
        profile: str = "ephemeral",
        profile_dir: str | None = None,
        profile_key: str | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.tenant_id = tenant_id
        self.engine = resolve_probe_engine(engine)
        self.requested_url = url
        self.headed = headed
        self.bypass_config = bypass_config
        self.manual_challenge = manual_challenge
        self.profile = profile
        self.profile_dir = profile_dir
        self.profile_key = profile_key
        self.created_at = time.monotonic()
        self.expires_at = self.created_at + ttl_seconds
        self.nav_trace: list[dict[str, Any]] = []
        self.commands: asyncio.Queue[_Command] = asyncio.Queue()
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.page: Any = None
        self.challenge_sink: Any = None
        self.open_error: dict[str, Any] | None = None
        self.snapshot: dict[str, Any] = {}

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def public(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _session_result(
            status=str(self.snapshot.get("status") or "ok"),
            ok=bool(self.snapshot.get("ok", True)),
            executed=True,
            error=self.snapshot.get("error"),
            notes=list(self.snapshot.get("notes") or []),
            session_id=self.id,
            tenant_id=self.tenant_id,
            engine=self.engine,
            headed=self.headed,
            profile=self.profile,
            profile_key=self.profile_key,
            requested_url=self.requested_url,
            ttl_seconds=round(self.remaining(), 1),
            **{
                k: v
                for k, v in self.snapshot.items()
                if k not in {"status", "ok", "error", "notes"}
            },
        )
        if extra:
            payload.update(extra)
        return payload

    async def _refresh(self) -> None:
        page = self.page
        final_url = str(getattr(page, "url", "") or self.requested_url)
        html = await _page_html(page) if page is not None else ""
        challenge = _observed_challenge(self.challenge_sink) or _classify_html(html)
        self.snapshot = {
            "status": "challenge" if challenge else "ok",
            "ok": not bool(challenge),
            "error": "challenge_detected" if challenge else None,
            "notes": [
                f"{self.profile} operator session; poll wait_challenge/extend to keep a headed captcha open",
                "does not return cookie values or profile filesystem paths",
            ],
            "final_url": final_url,
            "page_title": await _page_title(page) if page is not None else None,
            "challenge": challenge,
        }
        self.nav_trace.append(
            {
                "ts": round(time.monotonic() - self.created_at, 2),
                "url": final_url,
                "title": self.snapshot.get("page_title"),
                "challenge": challenge,
            }
        )
        self.nav_trace = self.nav_trace[-40:]

    async def _exec(self, command: _Command) -> dict[str, Any]:
        page = self.page
        if page is None:
            return _session_result(status="error", error="session_page_gone", session_id=self.id)
        kind = command.kind
        if kind == "wait":
            await asyncio.sleep(1.0)
        elif kind == "extend":
            self.expires_at = time.monotonic() + (
                HEADED_SESSION_TTL_SECONDS if self.headed else SESSION_TTL_SECONDS
            )
        elif kind == "reload":
            reload_fn = getattr(page, "reload", None)
            if callable(reload_fn):
                result = reload_fn()
                if hasattr(result, "__await__"):
                    await result
            else:
                await navigate(page, str(getattr(page, "url", "") or self.requested_url), {})
        elif kind == "wait_challenge":
            self.expires_at = max(
                self.expires_at,
                time.monotonic()
                + (HEADED_SESSION_TTL_SECONDS if self.headed else SESSION_TTL_SECONDS),
            )
            poll_budget = 20.0 if self.headed else 2.0
            deadline = time.monotonic() + min(poll_budget, self.remaining())
            while time.monotonic() < deadline:
                await self._refresh()
                if not self.snapshot.get("challenge"):
                    break
                await asyncio.sleep(1.0 if self.headed else 0.2)
        elif kind == "solve":
            from job_ftch.infrastructure.browser_probe import _make_solver

            solver = _make_solver(
                str(command.payload.get("solve") or "browser_wait"), self.bypass_config
            )
            if solver is None:
                return _session_result(
                    status="unsupported",
                    error="unsupported_solve",
                    session_id=self.id,
                )
            result = await solver.solve(
                page,
                challenge_type=str(self.snapshot.get("challenge") or "unknown"),
                url=str(getattr(page, "url", "") or self.requested_url),
            )
            await self._refresh()
            return self.public(extra={"captcha": _public_solve(result), "instruction": "solve"})
        elif kind == "navigate":
            target = str(command.payload.get("url") or "").strip()
            current = str(getattr(page, "url", "") or self.requested_url)
            if not target.startswith(("http://", "https://")) or not _same_origin(current, target):
                return _session_result(
                    status="unsupported",
                    error="same_origin_required",
                    session_id=self.id,
                    notes=["navigate is same-origin only"],
                )
            await check_ssrf(target)
            await navigate(page, target, {"timeout": NAV_TIMEOUT_MS, "wait": "domcontentloaded"})
        elif kind == "capture":
            return await self._capture(str(command.payload.get("artifact_type") or "text"))
        elif kind == "close":
            self.stop.set()
            await self._refresh()
            return self.public(extra={"status": "closed", "ok": True, "error": None})
        else:
            return _session_result(
                status="unsupported",
                error="unsupported_instruction",
                session_id=self.id,
            )
        await self._refresh()
        return self.public(extra={"instruction": kind})

    async def _capture(self, artifact_type: str) -> dict[str, Any]:
        page = self.page
        kind = (artifact_type or "text").strip().lower()
        if kind not in ARTIFACT_TYPES:
            return _session_result(
                status="unsupported",
                error="unsupported_artifact",
                session_id=self.id,
            )
        if kind == "trace":
            extra: dict[str, Any] = {"artifact_type": kind}
            context = getattr(page, "context", None)
            tracing = getattr(context, "tracing", None) if context is not None else None
            stop = getattr(tracing, "stop", None) if tracing is not None else None
            path: str | None = None
            if callable(stop):
                try:
                    with tempfile.NamedTemporaryFile(
                        prefix="job_ftch_trace_", suffix=".zip", delete=False
                    ) as handle:
                        zip_path = handle.name
                    result = stop(path=zip_path)
                    if hasattr(result, "__await__"):
                        await result
                    path = zip_path
                except Exception:
                    path = None
            if path is None:
                with tempfile.NamedTemporaryFile(
                    prefix="job_ftch_trace_",
                    suffix=".jsonl",
                    delete=False,
                    mode="w",
                    encoding="utf-8",
                ) as handle:
                    for row in self.nav_trace:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    path = handle.name
            extra["path"] = path
            extra["trace"] = {
                "events": list(self.nav_trace),
                "kind": "playwright_zip" if path.endswith(".zip") else "navigation_jsonl",
            }
            return self.public(extra=extra)
        await self._refresh()
        extra = {"artifact_type": kind}
        if kind == "text":
            extra["text"] = await _page_text(page)
        elif kind == "html":
            extra["html"] = (await _page_html(page))[:HTML_CAP]
        elif kind == "cookies_summary":
            names = await _cookie_names(page)
            extra["cookies_summary"] = {"count": len(names), "names": names}
        elif kind == "screenshot":
            shot_fn = getattr(page, "screenshot", None)
            if not callable(shot_fn):
                return _session_result(
                    status="unavailable",
                    error="screenshot_unavailable",
                    session_id=self.id,
                    artifact_type=kind,
                )
            raw = shot_fn(type="png")
            if hasattr(raw, "__await__"):
                raw = await raw
            with tempfile.NamedTemporaryFile(
                prefix="job_ftch_session_", suffix=".png", delete=False
            ) as handle:
                handle.write(bytes(raw or b""))
                extra["path"] = handle.name
        return self.public(extra=extra)

    async def run(self) -> None:
        notes = [f"{self.profile} operator session"]
        try:
            strategy = resolve_bypass(self.engine, self.bypass_config)
        except ValueError:
            self.open_error = _session_result(
                status="unavailable",
                error="engine_unavailable",
                notes=[*notes, f"bypass engine {self.engine!r} is not registered"],
                engine=self.engine,
                requested_url=self.requested_url,
            )
            self.ready.set()
            return
        self.challenge_sink = _attach_challenge_sink(strategy)
        persistent = self.profile in {"persistent", "domain"}
        config: dict[str, Any] = {
            "url": self.requested_url,
            "headless": not self.headed,
            "timeout": NAV_TIMEOUT_MS,
            "wait": "domcontentloaded",
            "persistent_context": persistent,
            "_bypass_strategy": self.challenge_sink,
        }
        if persistent and self.profile_dir:
            config["_profile_dir"] = self.profile_dir
        try:
            await check_ssrf(self.requested_url)
            async with open_page(config, bypass_strategy=strategy) as page:
                await navigate(page, self.requested_url, config)
                self.page = page
                await self._refresh()
                if self.manual_challenge:
                    await self._exec(_Command(kind="wait_challenge"))
                self.ready.set()
                while not self.stop.is_set() and self.remaining() > 0:
                    timeout = min(1.0, self.remaining())
                    try:
                        command = await asyncio.wait_for(self.commands.get(), timeout=timeout)
                    except TimeoutError:
                        continue
                    try:
                        payload = await self._exec(command)
                    except Exception as exc:
                        payload = _session_result(
                            status="error",
                            error=type(exc).__name__,
                            notes=[str(exc)[:200]],
                            session_id=self.id,
                        )
                    if command.future is not None and not command.future.done():
                        command.future.set_result(payload)
                    if command.kind == "close":
                        break
        except httpx.LocalProtocolError as exc:
            self.open_error = _session_result(
                status="error",
                error="ssrf_blocked",
                notes=[str(exc)],
                engine=self.engine,
                requested_url=self.requested_url,
            )
            self.ready.set()
        except (ImportError, RuntimeError) as exc:
            message = str(exc)
            if "patchright" in message.lower() or "playwright" in message.lower():
                self.open_error = _session_result(
                    status="unavailable",
                    error="browser_runtime_missing",
                    notes=[
                        message[:240],
                        "uv sync --extra browser && uv run patchright install chromium",
                    ],
                    engine=self.engine,
                    requested_url=self.requested_url,
                )
            else:
                self.open_error = _session_result(
                    status="error",
                    error=type(exc).__name__,
                    notes=[message[:240]],
                    engine=self.engine,
                    requested_url=self.requested_url,
                )
            self.ready.set()
        except Exception as exc:
            log.warning("browser_session.failed", error=type(exc).__name__)
            self.open_error = _session_result(
                status="error",
                error=type(exc).__name__,
                notes=[str(exc)[:240]],
                engine=self.engine,
                requested_url=self.requested_url,
            )
            self.ready.set()
        finally:
            self.page = None
            self.stop.set()
            if not self.ready.is_set():
                self.ready.set()


class OperatorBrowserSessionService:
    """Process-local registry for ephemeral operator browser sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, _LiveSession] = {}
        self._lock = asyncio.Lock()

    def _reap_locked(self) -> None:
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if session.stop.is_set()
            or session.remaining() <= 0
            or (session.task and session.task.done())
        ]
        for session_id in stale:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.stop.set()

    async def open(
        self,
        *,
        tenant_id: str,
        url: str,
        engine: str,
        headed: bool = False,
        bypass_config: dict[str, Any] | None = None,
        manual_challenge: bool = False,
        profile: str = "ephemeral",
    ) -> dict[str, Any]:
        normalized_profile = (profile or "ephemeral").strip().lower() or "ephemeral"
        if normalized_profile not in PROFILES:
            return _session_result(status="unsupported", error="unsupported_profile")
        profile_dir, profile_key = _profile_location(
            tenant_id=tenant_id, url=url, profile=normalized_profile
        )
        ttl = HEADED_SESSION_TTL_SECONDS if (headed or manual_challenge) else SESSION_TTL_SECONDS
        async with self._lock:
            self._reap_locked()
            if len(self._sessions) >= MAX_SESSIONS:
                return _session_result(
                    status="unavailable",
                    error="session_limit",
                    notes=[f"max {MAX_SESSIONS} live operator sessions"],
                )
        session = _LiveSession(
            tenant_id=tenant_id,
            url=url,
            engine=engine,
            headed=headed or manual_challenge,
            bypass_config=bypass_config,
            manual_challenge=manual_challenge,
            profile=normalized_profile,
            profile_dir=profile_dir,
            profile_key=profile_key,
            ttl_seconds=ttl,
        )
        session.task = asyncio.create_task(session.run())
        try:
            await asyncio.wait_for(session.ready.wait(), timeout=OPEN_DEADLINE_SECONDS)
        except TimeoutError:
            session.stop.set()
            return _session_result(
                status="timeout",
                error="session_open_deadline",
                engine=session.engine,
                requested_url=url,
            )
        if session.open_error is not None:
            return session.open_error
        async with self._lock:
            self._sessions[session.id] = session
        return session.public()

    async def get(self, session_id: str) -> dict[str, Any]:
        session = await self._find(session_id)
        if isinstance(session, dict):
            return session
        return session.public()

    async def continue_session(
        self, session_id: str, instruction: str | None = None
    ) -> dict[str, Any]:
        session = await self._find(session_id)
        if isinstance(session, dict):
            return session
        kind, payload = _parse_instruction(instruction)
        if kind == "unsupported":
            return _session_result(
                status="unsupported",
                error="unsupported_instruction",
                session_id=session_id,
                notes=["use wait|reload|wait_challenge|extend|solve|navigate <url>"],
            )
        return await self._ask(session, kind, payload)

    async def capture(self, session_id: str, artifact_type: str) -> dict[str, Any]:
        session = await self._find(session_id)
        if isinstance(session, dict):
            return session
        return await self._ask(session, "capture", {"artifact_type": artifact_type})

    async def close(self, session_id: str) -> dict[str, Any]:
        session = await self._find(session_id)
        if isinstance(session, dict):
            return session
        payload = await self._ask(session, "close", {})
        async with self._lock:
            self._sessions.pop(session_id, None)
        if session.task is not None:
            with _suppress():
                await asyncio.wait_for(session.task, timeout=5.0)
        payload["status"] = "closed"
        payload["ok"] = True
        return payload

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop.set()
            with _suppress():
                session.commands.put_nowait(_Command(kind="close"))
            if session.task is not None:
                with _suppress():
                    await asyncio.wait_for(session.task, timeout=5.0)

    async def _find(self, session_id: str) -> _LiveSession | dict[str, Any]:
        async with self._lock:
            self._reap_locked()
            session = self._sessions.get(session_id)
        if session is None:
            return _session_result(status="source_not_found", error="session_not_found")
        return session

    async def _ask(
        self,
        session: _LiveSession,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        await session.commands.put(_Command(kind=kind, payload=payload, future=future))
        try:
            return await asyncio.wait_for(future, timeout=COMMAND_DEADLINE_SECONDS)
        except TimeoutError:
            return _session_result(
                status="timeout",
                error="session_command_deadline",
                session_id=session.id,
            )


def _profile_location(*, tenant_id: str, url: str, profile: str) -> tuple[str | None, str | None]:
    if profile not in {"persistent", "domain"}:
        return None, None
    from job_ftch.config import get_settings

    root = get_settings().browser_profile_root
    host = urlparse(url).netloc.split(":")[0].lower() or "site"
    slug = "".join(ch if ch.isalnum() else "_" for ch in host).strip("_") or "site"
    key = f"{tenant_id}" if profile == "persistent" else f"{tenant_id}_{slug}"
    path = root / "operator" / key
    path.mkdir(parents=True, exist_ok=True)
    return str(path), key


def _parse_instruction(instruction: str | None) -> tuple[str, dict[str, Any]]:
    text = (instruction or "wait").strip()
    lowered = text.lower()
    if lowered in {"", "wait", "refresh"}:
        return "wait", {}
    if lowered == "reload":
        return "reload", {}
    if lowered in {"wait_challenge", "wait-challenge"}:
        return "wait_challenge", {}
    if lowered in {"extend", "keep_alive", "keep-alive"}:
        return "extend", {}
    if lowered in {"solve", "solve:browser_wait", "solve:auto"}:
        return "solve", {"solve": "browser_wait"}
    if lowered.startswith("solve:"):
        return "solve", {"solve": lowered.split(":", 1)[1]}
    if lowered.startswith("navigate "):
        return "navigate", {"url": text.split(None, 1)[1].strip()}
    return "unsupported", {}


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> bool:
        return True
