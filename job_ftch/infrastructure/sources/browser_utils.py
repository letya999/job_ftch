from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.contracts import BrowserSessionBypass
from job_ftch.config import get_settings
from job_ftch.infrastructure.bypass.proxy_pool import ProxyEndpoint
from job_ftch.infrastructure.sources.shared_limiters import browser_slot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from patchright.async_api import Browser, BrowserContext, Page, Playwright

log = structlog.get_logger()

_ATTACHED_OPERATOR_PAGE: ContextVar[Any | None] = ContextVar(
    "_ATTACHED_OPERATOR_PAGE",
    default=None,
)


def attach_operator_page(page: Any) -> Token[Any | None]:
    """Bind a live operator page for this task so open_page reuses it."""
    return _ATTACHED_OPERATOR_PAGE.set(page)


def reset_operator_page(token: Token[Any | None]) -> None:
    _ATTACHED_OPERATOR_PAGE.reset(token)


def resolve_identity_ua(config: dict[str, Any], persona_kw: dict[str, Any]) -> str | None:
    """The identity's coherent User-Agent, or ``None`` to keep the real one.

    TRACK A4: the session identity is the sole writer of UA. An explicit config
    UA wins, then the persona's aligned UA; when neither exists we return
    ``None`` so the caller omits the override and the engine keeps its real
    bundled Chromium UA - overriding with a hardcoded constant that cannot match
    the runtime is itself a fabricated-identity leak.
    """
    ua = config.get("user_agent") or persona_kw.get("user_agent")
    return str(ua) if ua else None


DEFAULT_WAIT = "domcontentloaded"
DEFAULT_WAIT_FALLBACK = "commit"

_CHALLENGE_DETECTOR_ATTR = "_job_ftch_challenge_response_detector"

_PATCHRIGHT_CANCELLATION_FIX_ATTR = "_job_ftch_cancellation_safe_inner_send"
_PATCHRIGHT_ROUTE_FIX_ATTR = "_job_ftch_cancellation_safe_route_handler"

BROWSER_KEYS = frozenset(
    {
        "wait",
        "wait_fallback",
        "timeout",
        "user_agent",
        "headless",
        "stealth",
        "actions",
        "warmup_url",
        "cookies",
        "disable_http2",
        "persistent_context",
        "channel",
        "viewport",
        "locale",
        "skip_ssl",
    }
)

_BROWSER_CLEANUP_TIMEOUT_SECONDS = 2.0
_BROWSER_DRIVER_STALE_SECONDS = 180
_BROWSER_TERMINATE_GRACE_SECONDS = 5.0


def normalize_browser_timeout_ms(value: Any) -> int:
    """Return a Playwright/Patchright-compatible integer timeout in milliseconds.

    Runtime/source configs can carry YAML floats such as ``15.0``. Patchright's
    Go transport rejects those when they reach a ``timeoutSeconds`` field, so
    browser-bound timeouts are normalized at the boundary instead of requiring
    every config source to use integer literals.
    """
    if isinstance(value, bool):
        raise TypeError("browser timeout must be numeric, not bool")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("browser timeout must be positive")
        return value
    if isinstance(value, float):
        if value <= 0:
            raise ValueError("browser timeout must be positive")
        return int(value) if value.is_integer() else int(round(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("browser timeout must not be empty")
        number = float(stripped)
        if number <= 0:
            raise ValueError("browser timeout must be positive")
        return int(number) if number.is_integer() else int(round(number))
    raise TypeError(f"unsupported browser timeout type: {type(value).__name__}")


async def _solve_page_challenge(controller: Any, page: Any, *, url: str) -> bool:
    """Solve once and preserve token-bearing page state when required."""
    if bool(getattr(controller, "challenge_solver_terminal", False)):
        return False
    solve = getattr(controller, "solve_page_challenge", None)
    if not callable(solve):
        return False
    return bool(await solve(page, url=url))


def _challenge_solution_requires_reload(controller: Any) -> bool:
    return bool(getattr(controller, "challenge_solution_requires_reload", True))


def _browser_proxy(proxy_url: str) -> dict[str, str]:
    return ProxyEndpoint(url=proxy_url).playwright_proxy()


# Substring markers (case-insensitive) matched against a descendant process's
# name and cmdline to recognise a browser or browser-driver this process
# spawned. Every candidate is already a descendant of the current PID, so the
# user's own Chrome (a separate process tree) can never match. Covers the whole
# bypass stack: patchright/playwright, nodriver, camoufox and cloakbrowser.
_BROWSER_PROC_MARKERS = (
    "chrome",
    "chromium",
    "headless_shell",
    "chrome_crashpad",
    "msedge",
    "patchright",
    "playwright",
    "camoufox",
    "firefox",
    "cloak",
    "nodriver",
)
# The patchright/playwright driver runtime is a bare ``node`` process. Matching
# "node" alone would also catch unrelated Node processes, so a driver marker
# must also be present in the cmdline before it is treated as ours.
_NODE_DRIVER_MARKERS = ("driver", "playwright", "patchright")

OVERLAY_SELECTORS = (
    '[class*="cookie-banner"]',
    '[class*="cookie-consent"]',
    '[class*="cookie-notice"]',
    '[class*="cookie-overlay"]',
    '[id*="cookie-banner"]',
    '[id*="cookie-consent"]',
    '[id*="cookie-notice"]',
    '[id*="onetrust-consent-sdk"]',
    '[id*="consent-banner"]',
    '[id*="consent-manager"]',
    '[class*="consent-banner"]',
    '[class*="consent-manager"]',
    '[role="dialog"][class*="cookie"]',
    '[role="dialog"][id*="cookie"]',
    "#didomi-host",
    ".cc-banner",
    ".cc-window",
    ".cc-revoke",
    ".cc-type-info",
)


async def _patchright_inner_send_cancellation_safe(
    channel: Any,
    method: str,
    timeout_calculator: Any,
    params: dict[str, Any] | None,
    return_as_dict: bool,
) -> Any:
    """Patchright's ``_inner_send`` with cancellation-safe callback cleanup.

    Patchright 1.61 leaves its protocol callback Future pending when the task
    awaiting ``asyncio.wait`` is cancelled.  The eventual browser response then
    sets an exception on that orphaned Future and Python emits ``Future
    exception was never retrieved``.  Cancel the callback before re-raising so
    Patchright's dispatcher discards the late protocol response.
    """
    from patchright._impl._connection import _augment_params

    if channel._connection._error:
        error = channel._connection._error
        channel._connection._error = None
        raise error
    callback = channel._connection._send_message_to_server(
        channel._object, method, _augment_params(params, timeout_calculator)
    )
    try:
        done, _ = await asyncio.wait(
            {channel._connection._transport.on_error_future, callback.future},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        callback.future.cancel()
        raise
    if not callback.future.done():
        callback.future.cancel()
    result = next(iter(done)).result()
    if not result:
        return None
    assert isinstance(result, dict)
    if return_as_dict:
        return result
    if len(result) == 0:
        return None
    assert len(result) == 1
    return result[next(iter(result))]


async def _patchright_route_handle_cancellation_safe(route_handler: Any, route: Any) -> bool:
    """Patchright ``RouteHandler.handle`` without invalid Future completion.

    Patchright cancels the completion Future that ``unroute_all(behavior="wait")``
    waits on when the owning task is cancelled.  Its stock ``finally`` then
    unconditionally calls ``set_result``, raising ``InvalidStateError`` from
    the listener and leaving the page-route task pending during teardown.
    """
    from patchright._impl._errors import rewrite_error
    from patchright._impl._helper import RouteHandlerInvocation, is_target_closed_error

    invocation = RouteHandlerInvocation(asyncio.get_running_loop().create_future(), route)
    route_handler._active_invocations.add(invocation)
    try:
        return await route_handler._handle_internal(route)
    except Exception as exc:
        if route_handler._ignore_exception:
            return False
        if is_target_closed_error(exc):
            optional_async_prefix = "await " if not route_handler._is_sync else ""
            raise rewrite_error(
                exc,
                (
                    f'"{exc}" while running route callback.\n'
                    "Consider awaiting "
                    f"`{optional_async_prefix}page.unroute_all(behavior='ignoreErrors')` "
                    "before the end of the test to ignore remaining routes in flight."
                ),
            ) from exc
        raise
    finally:
        if not invocation.complete.done():
            invocation.complete.set_result(None)
        route_handler._active_invocations.discard(invocation)


def _patchright_needs_legacy_cancellation_fix(send: Any) -> bool:
    return "timeout" not in inspect.signature(send).parameters


def _install_patchright_cancellation_fix() -> None:
    """Install the narrow Patchright cancellation workaround once per process."""
    try:
        from patchright._impl._connection import Channel, Connection
        from patchright._impl._helper import RouteHandler
    except ImportError:
        return
    if getattr(Channel, _PATCHRIGHT_CANCELLATION_FIX_ATTR, False):
        return
    # New Patchright versions pass an explicit timeout and already abort the
    # protocol callback on cancellation. Replacing that implementation with
    # the legacy workaround breaks its call signature and browser startup.
    if _patchright_needs_legacy_cancellation_fix(Connection._send_message_to_server):
        Channel._inner_send = _patchright_inner_send_cancellation_safe
    setattr(Channel, _PATCHRIGHT_CANCELLATION_FIX_ATTR, True)
    if not getattr(RouteHandler, _PATCHRIGHT_ROUTE_FIX_ATTR, False):
        RouteHandler.handle = _patchright_route_handle_cancellation_safe
        setattr(RouteHandler, _PATCHRIGHT_ROUTE_FIX_ATTR, True)


async def _await_browser_cleanup(awaitable: Any, *, label: str) -> None:
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.wait_for(task, timeout=_BROWSER_CLEANUP_TIMEOUT_SECONDS)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        log.warning("browser.cleanup_timeout", step=label)
    except asyncio.CancelledError:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        log.warning("browser.cleanup_cancelled", step=label)
    except Exception as exc:
        log.debug("browser.cleanup_failed", step=label, error=type(exc).__name__)


async def _call_browser_cleanup(target: Any, method_name: str, *, label: str) -> None:
    method = getattr(target, method_name, None)
    if not callable(method):
        return
    await _await_browser_cleanup(method(), label=label)


async def _unroute_page_before_close(page: Any) -> None:
    """Drain in-flight route callbacks before the page transport is closed."""
    unroute_all = getattr(page, "unroute_all", None)
    if not callable(unroute_all):
        return
    await _await_browser_cleanup(unroute_all(behavior="ignoreErrors"), label="unroute_all")


def _proc_signature(proc: Any) -> str:
    """Lower-cased ``name + cmdline`` for marker matching; psutil-error safe."""
    try:
        name = proc.name() or ""
    except Exception:
        name = ""
    try:
        cmdline = " ".join(proc.cmdline() or [])
    except Exception:
        cmdline = ""
    return f"{name} {cmdline}".casefold()


def _is_browser_proc(proc: Any) -> bool:
    """Whether a descendant process is one of our browser/driver children."""
    signature = _proc_signature(proc)
    if not signature.strip():
        return False
    if any(marker in signature for marker in _BROWSER_PROC_MARKERS):
        return True
    return "node" in signature and any(marker in signature for marker in _NODE_DRIVER_MARKERS)


def _browser_descendants(*, spare_pids: frozenset[int] = frozenset()) -> list[Any]:
    """Return browser/driver descendants of the current process.

    Only ``psutil.Process().children(recursive=True)`` is inspected, so every
    result is guaranteed to be a process this interpreter spawned; the user's
    own Chrome is in a separate process tree and can never appear here.
    """
    try:
        import psutil
    except ImportError:
        return []
    try:
        children = psutil.Process().children(recursive=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("browser.descendant_probe_failed", error=type(exc).__name__)
        return []
    return [child for child in children if child.pid not in spare_pids and _is_browser_proc(child)]


def _select_stale_driver_pids(
    snapshot: list[tuple[int, int, float, bool]], *, min_age_seconds: int
) -> list[int]:
    """Pick stale, orphaned browser-driver leaves from a process snapshot.

    ``snapshot`` is ``(pid, ppid, age_seconds, is_browser)`` for every current
    descendant. A pid is selected only when it is a browser process, older than
    ``min_age_seconds`` and has no live child (no descendant lists it as ppid),
    i.e. the browser it managed already exited. Pure and unit-testable so the
    reaper logic can be exercised without spawning real browsers.
    """
    pids = {pid for pid, _ppid, _age, _is_browser in snapshot}
    parent_pids = {ppid for _pid, ppid, _age, _is_browser in snapshot}
    return [
        pid
        for pid, _ppid, age, is_browser in snapshot
        if is_browser and age >= min_age_seconds and pid not in parent_pids and _ppid not in pids
    ]


def _current_descendant_snapshot() -> tuple[list[tuple[int, int, float, bool]], dict[int, Any]]:
    """Snapshot current descendants for the stale-driver selector."""
    try:
        import psutil
    except ImportError:
        return [], {}
    try:
        children = psutil.Process().children(recursive=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("browser.driver_reap_probe_failed", error=type(exc).__name__)
        return [], {}
    now = time.time()
    snapshot: list[tuple[int, int, float, bool]] = []
    by_pid: dict[int, Any] = {}
    for child in children:
        try:
            ppid = child.ppid()
            age = now - child.create_time()
        except Exception:  # noqa: BLE001
            continue
        snapshot.append((child.pid, ppid, age, _is_browser_proc(child)))
        by_pid[child.pid] = child
    return snapshot, by_pid


def reap_stale_browser_drivers(*, min_age_seconds: int = _BROWSER_DRIVER_STALE_SECONDS) -> None:
    """Terminate stale, orphaned browser-driver descendants of this process.

    Safe to call while other browser sessions run concurrently (Phase-7B item
    workers keep several ``open_page`` contexts alive at once): only descendants
    that are both older than ``min_age_seconds`` AND childless (their managed
    browser already exited) are touched, so live sibling scrapes are untouched.
    Cross-platform replacement for the former POSIX-only patchright reaper;
    covers patchright, nodriver, camoufox and cloakbrowser. Never raises.
    """
    snapshot, by_pid = _current_descendant_snapshot()
    if not snapshot:
        return
    try:
        import psutil
    except ImportError:
        return
    targets = []
    for pid in _select_stale_driver_pids(snapshot, min_age_seconds=min_age_seconds):
        proc = by_pid.get(pid)
        if proc is None:
            continue
        try:
            proc.terminate()
            targets.append(proc)
            log.warning("browser.stale_driver_reaped", pid=pid)
        except Exception as exc:  # noqa: BLE001
            log.debug("browser.stale_driver_reap_failed", pid=pid, error=type(exc).__name__)
    if not targets:
        return
    _gone, alive = psutil.wait_procs(targets, timeout=_BROWSER_TERMINATE_GRACE_SECONDS)
    for proc in alive:
        with suppress(Exception):
            proc.kill()


def terminate_browser_descendants(
    *,
    spare_pids: frozenset[int] = frozenset(),
    grace: float = _BROWSER_TERMINATE_GRACE_SECONDS,
) -> None:
    """Force-terminate every browser/driver descendant of this process.

    This is the full teardown path for process/run exit, where no browser
    session is expected to remain in use. It must NOT be wired into the
    per-``open_page`` finally: under Phase-7B concurrency that would kill
    sibling scrapes' live browsers. Because only current-process descendants
    are targeted, the user's own Chrome is never affected. Idempotent.
    """
    try:
        import psutil
    except ImportError:
        return
    targets = _browser_descendants(spare_pids=spare_pids)
    if not targets:
        return
    for proc in targets:
        with suppress(Exception):
            proc.terminate()
    _gone, alive = psutil.wait_procs(targets, timeout=grace)
    for proc in alive:
        with suppress(Exception):
            proc.kill()
    log.warning("browser.descendants_terminated", count=len(targets))


def _is_browser_connect_failure(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return (
        "failed to connect to browser" in message
        or "browser closed" in message
        or "target page, context or browser has been closed" in message
    )


async def _launch_browser_with_recovery(awaitable_factory: Any, *, label: str) -> Any:
    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    try:
        return await await_with_source_deadline(awaitable_factory())
    except Exception as exc:
        if not _is_browser_connect_failure(exc):
            raise
        log.warning("browser.launch_recovering", step=label, error=type(exc).__name__)
        reap_stale_browser_drivers()
        await asyncio.sleep(0.5)
        return await await_with_source_deadline(awaitable_factory())


@asynccontextmanager
async def open_page(
    config: dict[str, Any],
    *,
    use_proxy: bool = False,
    bypass_strategy: Any = None,
) -> AsyncIterator[Any]:
    """
    High-level entry point to open a browser page with optional stealth and proxy.
    """
    attached = _ATTACHED_OPERATOR_PAGE.get()
    if attached is not None:
        yield attached
        return
    prepare_config = getattr(bypass_strategy, "prepare_browser_config", None)
    if callable(prepare_config):
        config = prepare_config(config)
    # All browser users (monitors, DOM/API sniffing and detail enrichment)
    # share one settings-driven limiter.  A local fixed semaphore let each
    # path create its own Chromium processes and ignored the environment.
    async with browser_slot(get_settings().career_site_browser_concurrency):
        session_bypass = bypass_strategy
        session_resolver = getattr(bypass_strategy, "get_browser_session_bypass", None)
        if callable(session_resolver):
            session_bypass = session_resolver()
        if session_bypass is not None and isinstance(session_bypass, BrowserSessionBypass):
            effective_proxy = use_proxy or bool(getattr(bypass_strategy, "uses_proxy", False))
            session_config = dict(config)
            proxy_url = getattr(bypass_strategy, "current_proxy_url", None)
            if effective_proxy and proxy_url:
                session_config["_proxy_url"] = proxy_url
            session_owner = (
                bypass_strategy
                if callable(getattr(bypass_strategy, "open_page", None))
                else session_bypass
            )
            manager = session_owner.open_page(session_config, use_proxy=effective_proxy)
            from job_ftch.infrastructure.sources.source_deadline import (
                await_with_source_deadline,
            )

            enter_task = asyncio.create_task(manager.__aenter__())
            # Start the context manager before applying the deadline.  This
            # guarantees that cancellation can unwind its ``finally`` block
            # and release a browser/proxy resource acquired during entry.
            await asyncio.sleep(0)
            try:
                page = await await_with_source_deadline(enter_task)
            except BaseException as exc:
                # A deadline can cancel __aenter__ before it yields a page.
                # Explicitly exit the session manager so bypass strategies can
                # release their browser/proxy resources on that path too.
                if not enter_task.done():
                    enter_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await enter_task
                with suppress(Exception):
                    await manager.__aexit__(type(exc), exc, exc.__traceback__)
                raise
            try:
                if bypass_strategy is not session_bypass:
                    await await_with_source_deadline(bypass_strategy.apply_page(page))
                yield page
            finally:
                await _capture_session_state(bypass_strategy, page)
                try:
                    await manager.__aexit__(None, None, None)
                except Exception as exc:
                    log.warning("browser.session_bypass_cleanup_failed", error=str(exc))
            return

        async_playwright = _load_async_playwright(
            prefer_patchright=_prefer_patchright(config, bypass_strategy)
        )

        persistent = config.get("persistent_context", False)
        if persistent:
            async with (
                async_playwright() as pw,
                _open_persistent_page(
                    pw, config, use_proxy=use_proxy, bypass_strategy=bypass_strategy
                ) as p,
            ):
                try:
                    yield p
                finally:
                    await _capture_session_state(bypass_strategy, p)
            return

        async with (
            async_playwright() as pw,
            _open_playwright_page(
                pw, config, use_proxy=use_proxy, bypass_strategy=bypass_strategy
            ) as page,
        ):
            try:
                yield page
            finally:
                await _capture_session_state(bypass_strategy, page)


async def _capture_session_state(bypass_strategy: Any, page: Any) -> None:
    capture = getattr(bypass_strategy, "capture_session_state", None)
    if not callable(capture):
        return
    try:
        await capture(page)
    except Exception as exc:
        log.warning("browser.session_capture_failed", error=type(exc).__name__)


async def _resolve_bypass_context(config: dict[str, Any]) -> Any:
    """Resolve a BypassContext from config if a URL is available."""
    existing = config.get("_bypass_context")
    if existing is not None:
        return existing
    url = config.get("warmup_url") or config.get("url")
    if not url:
        return None
    try:
        from job_ftch.infrastructure.bypass.context import BypassContext

        return await BypassContext.for_url(url, config=config)
    except Exception:
        return None


def _prefer_patchright(config: dict[str, Any], bypass_strategy: Any) -> bool:
    if bool(config.get("_patchright_required")):
        return True
    return bool(
        bypass_strategy is not None and getattr(bypass_strategy, "requires_process_identity", False)
    )


def _load_async_playwright(*, prefer_patchright: bool) -> Any:
    del prefer_patchright  # The production image installs Patchright's Chromium only.
    try:
        from patchright.async_api import async_playwright as patchright_async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "patchright is required for browser-backed scraping. "
            "Install: pip install patchright && patchright install chromium"
        ) from exc
    _install_patchright_cancellation_fix()
    return patchright_async_playwright


def _playwright_launcher(pw: Playwright, launch_kwargs: dict[str, Any]) -> Any:
    launch_kwargs.pop("_cloakbrowser_backend", None)
    launch_kwargs.pop("_patchright_required", None)
    # Camoufox accepts this high-level option; Playwright/Patchright do not.
    launch_kwargs.pop("geoip", None)
    browser_engine = str(launch_kwargs.pop("browser_engine", "chromium")).strip().lower()
    if browser_engine == "chromium":
        return pw.chromium
    if browser_engine == "firefox":
        launch_kwargs.pop("channel", None)
        return pw.firefox
    if browser_engine == "webkit":
        launch_kwargs.pop("channel", None)
        return pw.webkit
    msg = f"Unsupported Playwright browser engine: {browser_engine}"
    raise ValueError(msg)


@asynccontextmanager
async def _open_playwright_page(
    pw: Playwright,
    config: dict[str, Any],
    *,
    use_proxy: bool = False,
    bypass_strategy: Any = None,
) -> AsyncIterator[Page]:
    settings = get_settings()
    headless = config.get("headless", settings.browser_headless)
    channel = config.get("channel") or settings.browser_channel or None
    stealth = config.get("stealth", True)

    bypass_ctx = await _resolve_bypass_context(config)

    launch_args = []
    if stealth:
        launch_args.append("--disable-blink-features=AutomationControlled")

    if config.get("disable_http2"):
        launch_args.append("--disable-http2")

    if headless:
        launch_args.append("--headless=new")

    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": launch_args,
        "timeout": settings.browser_context_timeout_ms,
    }
    if channel:
        launch_kwargs["channel"] = channel

    persona_kw = bypass_ctx.context_kwargs() if bypass_ctx else {}
    context_kwargs: dict[str, Any] = {
        "viewport": config.get("viewport")
        or persona_kw.get("viewport", {"width": 1440, "height": 900}),
        "locale": config.get("locale") or persona_kw.get("locale", "en-US"),
        "ignore_https_errors": config.get("skip_ssl", True),
    }
    identity_ua = resolve_identity_ua(config, persona_kw)
    if identity_ua:
        context_kwargs["user_agent"] = identity_ua
    if bypass_strategy and getattr(bypass_strategy, "requires_process_identity", False):
        # Context identity does not cover the bootstrap requests of SharedWorker
        # scripts. Patchright opts in so those process-level requests remain
        # coherent with the page; other engines keep their native launch path.
        launch_kwargs["_process_identity_user_agent"] = identity_ua
        launch_kwargs["_process_identity_locale"] = context_kwargs["locale"]
    if config.get("timezone_id"):
        context_kwargs["timezone_id"] = config["timezone_id"]
    if persona_kw.get("timezone_id") and "timezone_id" not in config:
        context_kwargs["timezone_id"] = persona_kw["timezone_id"]

    if use_proxy:
        proxy_url = config.get("_proxy_url") or os.environ.get("JOB_FTCH_HTTP_PROXY")
        if proxy_url:
            context_kwargs["proxy"] = _browser_proxy(str(proxy_url))

    if bypass_strategy:
        launch_kwargs = bypass_strategy.apply_browser_args(launch_kwargs)

    browser_launcher = _playwright_launcher(pw, launch_kwargs)
    reap_stale_browser_drivers()
    browser: Browser | None = None
    if channel:
        try:
            browser = await _launch_browser_with_recovery(
                lambda: browser_launcher.launch(**launch_kwargs),
                label="browser.launch.channel",
            )
        except Exception:
            log.info("browser.channel_fallback", channel=channel)
            launch_kwargs.pop("channel", None)
            browser = None
    if browser is None:
        browser = await _launch_browser_with_recovery(
            lambda: browser_launcher.launch(**launch_kwargs),
            label="browser.launch",
        )

    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    if bypass_ctx:
        reported_version = str(getattr(browser, "version", ""))
        bypass_ctx.align_browser_runtime("chromium", reported_version)
        # The persona may be version-aligned only after the real browser has
        # launched.  Re-project its UA into the context even when
        # prepare_browser_config already filled an older persona UA; otherwise
        # window headers say one Chrome major while workers expose the native
        # runtime major.
        if identity_ua:
            context_kwargs["user_agent"] = bypass_ctx.persona.ua

    context: BrowserContext = await await_with_source_deadline(
        browser.new_context(**context_kwargs)
    )

    timeout_ms = normalize_browser_timeout_ms(
        config.get("timeout", settings.browser_default_timeout_ms)
    )
    context.set_default_timeout(timeout_ms)

    if config.get("cookies"):
        await context.add_cookies(config["cookies"])

    page: Page = await await_with_source_deadline(context.new_page())

    # When the adaptive controller owns page hardening it injects the persona
    # blob itself (once) inside ``apply_page``. Calling ``bypass_ctx.on_page``
    # as well would inject a second, possibly conflicting copy, so skip it.
    if bypass_ctx and not getattr(bypass_strategy, "owns_page_hardening", False):
        await bypass_ctx.on_page(page)
    if bypass_strategy:
        await bypass_strategy.apply_page(page)

    try:
        if config.get("warmup_url"):
            stats = config.get("_pipeline_stats")
            if stats is not None:
                stats.browser_navigations_attempted += 1
            from job_ftch.infrastructure.network.ssrf_guard import check_ssrf

            if not config.get("_allow_private_selfcheck_fixture"):
                await check_ssrf(config["warmup_url"])
            await page.goto(config["warmup_url"])
        yield page
    finally:
        # Closing the browser alone leaves Page/Context transports behind on
        # Windows when navigation was cancelled by a source deadline.  Close
        # the innermost resources first; every close is best-effort because a
        # target may already have been terminated by Patchright.
        await _unroute_page_before_close(page)
        await _call_browser_cleanup(page, "close", label="page.close")
        await _call_browser_cleanup(context, "close", label="context.close")
        await _call_browser_cleanup(browser, "close", label="browser.close")
        reap_stale_browser_drivers()


@asynccontextmanager
async def _open_persistent_page(
    pw: Playwright,
    config: dict[str, Any],
    *,
    use_proxy: bool = False,
    bypass_strategy: Any = None,
) -> AsyncIterator[Page]:
    """
    Opens a page using launch_persistent_context for better stealth.
    """
    import tempfile

    from job_ftch.config import get_settings

    settings = get_settings()
    shared_profile_dir = config.get("_profile_dir")
    user_data_dir = (
        str(shared_profile_dir) if shared_profile_dir else tempfile.mkdtemp(prefix="pw_profile_")
    )
    owns_profile_dir = not bool(shared_profile_dir)
    headless = config.get("headless", settings.browser_headless)
    channel = config.get("channel") or settings.browser_channel or None
    stealth = config.get("stealth", True)

    bypass_ctx = await _resolve_bypass_context(config)
    persona_kw = bypass_ctx.context_kwargs() if bypass_ctx else {}

    args = []
    if stealth:
        args.append("--disable-blink-features=AutomationControlled")
    if config.get("disable_http2"):
        args.append("--disable-http2")
    if headless:
        args.append("--headless=new")

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "args": args,
        "viewport": config.get("viewport")
        or persona_kw.get("viewport", {"width": 1440, "height": 900}),
        "locale": config.get("locale") or persona_kw.get("locale", "en-US"),
        "ignore_https_errors": config.get("skip_ssl", True),
        "timeout": settings.browser_context_timeout_ms,
    }
    identity_ua = resolve_identity_ua(config, persona_kw)
    if identity_ua:
        launch_kwargs["user_agent"] = identity_ua
    if channel:
        launch_kwargs["channel"] = channel
    if config.get("timezone_id"):
        launch_kwargs["timezone_id"] = config["timezone_id"]
    if persona_kw.get("timezone_id") and "timezone_id" not in config:
        launch_kwargs["timezone_id"] = persona_kw["timezone_id"]

    if use_proxy:
        proxy_url = config.get("_proxy_url") or os.environ.get("JOB_FTCH_HTTP_PROXY")
        if proxy_url:
            launch_kwargs["proxy"] = _browser_proxy(str(proxy_url))

    if bypass_strategy:
        launch_kwargs = bypass_strategy.apply_browser_args(launch_kwargs)

    browser_launcher = _playwright_launcher(pw, launch_kwargs)
    reap_stale_browser_drivers()
    context: BrowserContext | None = None
    if channel:
        try:
            context = await _launch_browser_with_recovery(
                lambda: browser_launcher.launch_persistent_context(**launch_kwargs),
                label="browser.launch_persistent_context.channel",
            )
        except Exception:
            log.info("browser.channel_fallback", channel=channel)
            launch_kwargs.pop("channel", None)
            context = None
    if context is None:
        context = await _launch_browser_with_recovery(
            lambda: browser_launcher.launch_persistent_context(**launch_kwargs),
            label="browser.launch_persistent_context",
        )

    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    timeout_ms = normalize_browser_timeout_ms(
        config.get("timeout", settings.browser_default_timeout_ms)
    )
    context.set_default_timeout(timeout_ms)

    if config.get("cookies"):
        await context.add_cookies(config["cookies"])

    page = (
        context.pages[0] if context.pages else await await_with_source_deadline(context.new_page())
    )

    # See note in _open_playwright_page: skip the duplicate persona injection
    # when the adaptive controller already owns page hardening.
    if bypass_ctx and not getattr(bypass_strategy, "owns_page_hardening", False):
        await bypass_ctx.on_page(page)
    if bypass_strategy:
        await bypass_strategy.apply_page(page)

    try:
        if config.get("warmup_url"):
            stats = config.get("_pipeline_stats")
            if stats is not None:
                stats.browser_navigations_attempted += 1
            from job_ftch.infrastructure.network.ssrf_guard import check_ssrf

            if not config.get("_allow_private_selfcheck_fixture"):
                await check_ssrf(config["warmup_url"])
            await page.goto(config["warmup_url"])
        yield page
    finally:
        await _unroute_page_before_close(page)
        await _call_browser_cleanup(page, "close", label="page.close")
        await _call_browser_cleanup(context, "close", label="context.close")
        reap_stale_browser_drivers()
        import shutil

        if owns_profile_dir:
            with suppress(Exception):
                shutil.rmtree(user_data_dir, ignore_errors=True)


async def navigate(page: Page, url: str, config: dict[str, Any]) -> None:
    """
    Navigate to a URL with fallback wait strategies.

    Some anti-bot systems (e.g. Ozon's cookie/JS challenge) return 403/503 on the first
    hit and serve 200 once the challenge cookie is set by the in-page JS. For those
    statuses we wait and reload before giving up, instead of failing immediately.
    Raises RuntimeError if the page still responds with an anti-bot status (403, 401, 429, 503).
    """
    wait = config.get("wait", DEFAULT_WAIT)
    wait_fallback = config.get("wait_fallback", DEFAULT_WAIT_FALLBACK)
    from job_ftch.config import get_settings

    settings = get_settings()
    timeout = normalize_browser_timeout_ms(
        config.get("timeout", settings.browser_default_timeout_ms)
    )
    challenge_retries = config.get("challenge_retries", settings.browser_challenge_retries)
    challenge_wait_ms = config.get("challenge_wait_ms", 6000)
    blocked = (403, 401, 429, 503)
    # Statuses worth a wait-and-reload: a JS/cookie challenge (403/503) or a cookie-warmup
    # rate-limit (429) that clears once the anti-bot cookies are set by the in-page JS.
    # 401 is excluded — a reload does not resolve genuine authentication failures.
    challenge = (403, 429, 503)

    stats = config.get("_pipeline_stats")
    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    if not config.get("_allow_private_selfcheck_fixture"):
        await install_challenge_response_detector(
            page,
            url=url,
            controller=config.get("_bypass_strategy"),
            surface="browser",
        )

    try:
        from job_ftch.infrastructure.network.ssrf_guard import check_ssrf

        if not config.get("_allow_private_selfcheck_fixture"):
            await check_ssrf(url)
        if stats is not None:
            stats.browser_navigations_attempted += 1
        resp = await await_with_source_deadline(page.goto(url, wait_until=wait, timeout=timeout))
    except Exception as exc:
        if not wait_fallback or wait == wait_fallback:
            raise
        log.warning("browser.navigate_fallback", url=url, error=str(exc), fallback=wait_fallback)
        if stats is not None:
            stats.browser_navigations_attempted += 1
        resp = await await_with_source_deadline(
            page.goto(url, wait_until=wait_fallback, timeout=timeout)
        )

    attempt = 0
    while resp is not None and resp.status in challenge and attempt < challenge_retries:
        attempt += 1
        log.info("browser.challenge_retry", url=url, status=resp.status, attempt=attempt)
        from job_ftch.infrastructure.sources.source_deadline import sleep_with_source_deadline

        await sleep_with_source_deadline(challenge_wait_ms / 1000)
        resp = await await_with_source_deadline(
            page.goto(url, wait_until=wait_fallback or wait, timeout=timeout)
        )

    if resp is not None and resp.status in challenge:
        controller = config.get("_bypass_strategy")
        if await _solve_page_challenge(controller, page, url=url) and (
            _challenge_solution_requires_reload(controller)
        ):
            resp = await await_with_source_deadline(
                page.goto(url, wait_until=wait_fallback or wait, timeout=timeout)
            )

    if resp is not None and resp.status not in blocked and await _page_has_captcha_marker(page):
        controller = config.get("_bypass_strategy")
        if await _solve_page_challenge(controller, page, url=url) and (
            _challenge_solution_requires_reload(controller)
        ):
            resp = await await_with_source_deadline(
                page.goto(url, wait_until=wait_fallback or wait, timeout=timeout)
            )

    # Session-owned engines such as nodriver may not return a Playwright-style
    # navigation response. In that case the response detector is the only typed
    # signal that a Cloudflare challenge was seen; honor it before treating an
    # empty rendered page as a normal discovery miss.
    await asyncio.sleep(0)
    controller = config.get("_bypass_strategy")
    observed = getattr(controller, "observed_challenge_type", None)
    if isinstance(observed, str) and observed.strip():
        log.info("browser.observed_challenge_solve", url=url, challenge_type=observed)
        if await _solve_page_challenge(controller, page, url=url) and (
            _challenge_solution_requires_reload(controller)
        ):
            resp = await await_with_source_deadline(
                page.goto(url, wait_until=wait_fallback or wait, timeout=timeout)
            )

    if resp is not None and resp.status in blocked:
        await _observe_blocked_navigation(
            page,
            resp,
            controller=config.get("_bypass_strategy"),
        )
        raise RuntimeError(f"Browser navigation blocked with status {resp.status}")


def is_blocked_navigation_error(exc: BaseException) -> bool:
    """True when navigate() gave up on a 401/403/429/503 response."""
    return isinstance(exc, RuntimeError) and str(exc).startswith(
        "Browser navigation blocked with status"
    )


def blocked_navigation_status(exc: BaseException) -> int | None:
    if not is_blocked_navigation_error(exc):
        return None
    try:
        return int(str(exc).rsplit(" ", 1)[-1])
    except ValueError:
        return None


async def _observe_blocked_navigation(
    page: Any,
    resp: Any,
    *,
    controller: Any = None,
) -> None:
    """Classify body+status onto the bypass before navigate() raises."""
    if controller is None:
        return
    observed = getattr(controller, "observed_challenge_type", None)
    if isinstance(observed, str) and observed.strip():
        return
    setter = getattr(controller, "set_observed_challenge_type", None)
    if not callable(setter):
        return
    body = await _blocked_navigation_body(page, resp)
    headers: dict[str, str] = {}
    raw_headers = getattr(resp, "headers", None)
    if isinstance(raw_headers, dict):
        headers = {str(key): str(value) for key, value in raw_headers.items()}
    from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge

    detection = classify_challenge(
        surface="browser",
        status_code=int(getattr(resp, "status", 0) or 0) or None,
        headers=headers or None,
        body=body,
    )
    if not detection.detected:
        return
    challenge_type = str(detection.challenge_type or "unknown")
    maybe = setter(challenge_type)
    if hasattr(maybe, "__await__"):
        await maybe


async def _blocked_navigation_body(page: Any, resp: Any) -> bytes | None:
    for attr in ("body", "text"):
        fn = getattr(resp, attr, None)
        if not callable(fn):
            continue
        try:
            raw = fn()
            if hasattr(raw, "__await__"):
                raw = await raw
        except Exception:
            continue
        if isinstance(raw, bytes):
            return raw[:100_000]
        if isinstance(raw, str) and raw:
            return raw.encode("utf-8", errors="ignore")[:100_000]
    content_fn = getattr(page, "content", None)
    if not callable(content_fn):
        return None
    try:
        html = content_fn()
        if hasattr(html, "__await__"):
            html = await html
    except Exception:
        return None
    if not html:
        return None
    return str(html).encode("utf-8", errors="ignore")[:100_000]


async def install_challenge_response_detector(
    page: Any,
    *,
    url: str,
    controller: Any = None,
    surface: str = "browser",
) -> None:
    """Attach a token-safe response classifier to a page once."""
    if getattr(page, _CHALLENGE_DETECTOR_ATTR, False):
        return
    if not hasattr(page, "on"):
        return
    setattr(page, _CHALLENGE_DETECTOR_ATTR, True)
    started_at = time.monotonic()

    async def _inspect_response(response: Any) -> None:
        try:
            status_code = int(getattr(response, "status", 0) or 0)
            headers = dict(getattr(response, "headers", {}) or {})
            body = await _challenge_probe_body(response, status_code, headers)
            from job_ftch.infrastructure.bypass.challenge_classifier import (
                classify_challenge,
                emit_challenge_detection,
            )

            detection = classify_challenge(
                surface=surface,
                status_code=status_code,
                headers=headers,
                body=body,
                started_at=started_at,
            )
            if not detection.detected:
                return
            response_url = str(getattr(response, "url", "") or url)
            emit_challenge_detection(urlparse(response_url).netloc.lower(), detection)
            setter = getattr(controller, "set_observed_challenge_type", None)
            if detection.challenge_type and callable(setter):
                maybe_result = setter(detection.challenge_type)
                if hasattr(maybe_result, "__await__"):
                    await maybe_result
        except Exception as exc:
            log.debug("browser.challenge_response_detector_failed", error=str(exc))

    def _schedule(response: Any) -> None:
        task = asyncio.create_task(_inspect_response(response))
        task.add_done_callback(_swallow_detector_exception)

    page.on("response", _schedule)


async def _challenge_probe_body(
    response: Any, status_code: int, headers: dict[str, str]
) -> bytes | None:
    """Read short HTML only when it can improve challenge classification."""
    content_type = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
    request = getattr(response, "request", None)
    resource_type = str(getattr(request, "resource_type", "") or "").lower()
    suspicious_status = status_code in {202, 401, 403, 429, 498, 499, 503}
    document_html = resource_type == "document" and (
        "text/html" in content_type or "application/xhtml" in content_type or not content_type
    )
    if not suspicious_status and not document_html:
        return None
    body_fn = getattr(response, "body", None)
    if not callable(body_fn):
        return None
    try:
        body = await body_fn()
    except Exception:
        return None
    return bytes(body or b"")[:100_000]


def _swallow_detector_exception(finished: asyncio.Task[Any]) -> None:
    with suppress(Exception):
        finished.exception()


async def _page_has_captcha_marker(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                  const selectors = [
                    '.g-recaptcha',
                    '#g-recaptcha',
                    '[data-sitekey]',
                    'iframe[src*="recaptcha"]',
                    'iframe[src*="hcaptcha"]',
                    'iframe[src*="turnstile"]',
                    'script[src*="recaptcha"]',
                    'script[src*="hcaptcha"]',
                    'script[src*="turnstile"]'
                  ];
                  return selectors.some((selector) => document.querySelector(selector));
                }
                """
            )
        )
    except Exception:
        return False


async def dismiss_overlays(page: Page) -> None:
    """
    Remove common cookie banners and overlays from the DOM.
    """
    selector = ", ".join(OVERLAY_SELECTORS)
    try:
        await page.evaluate(
            "(sel) => { "
            "  document.querySelectorAll(sel).forEach(el => el.remove()); "
            "  document.body.style.overflow = 'auto'; "
            "  document.documentElement.style.overflow = 'auto'; "
            "}",
            selector,
        )
    except Exception as exc:
        log.debug("browser.dismiss_overlays_failed", error=str(exc))


async def run_actions(page: Page, actions: list[dict[str, Any]]) -> None:
    """
    Execute a sequence of browser actions.
    """
    for action in actions:
        kind = action.get("action")
        if not kind:
            continue

        try:
            await _execute_action(page, action, kind)
        except Exception as exc:
            log.warning("browser.action_failed", action=kind, error=str(exc))


async def _execute_action(page: Page, action: dict[str, Any], kind: str) -> None:
    """
    Internal dispatcher for action execution.
    """
    if kind == "remove":
        selector = action.get("selector")
        if selector:
            await page.evaluate(
                f"() => document.querySelectorAll('{selector}').forEach(el => el.remove())"
            )

    elif kind == "click":
        selector = action.get("selector")
        if selector:
            await page.click(selector, timeout=action.get("timeout", 5000))

    elif kind == "wait":
        seconds = action.get("seconds", 1)
        await asyncio.sleep(seconds)

    elif kind == "evaluate":
        expression = action.get("expression")
        if expression:
            await page.evaluate(expression)

    elif kind == "dismiss_overlays":
        await dismiss_overlays(page)

    elif kind == "repeat":
        await _execute_repeat(page, action)


async def _execute_repeat(page: Page, action: dict[str, Any]) -> None:
    """
    Repeatedly execute an action (e.g. click "Load More").
    """
    times = action.get("times", 5)
    inner_action = action.get("inner")
    if not inner_action:
        return

    inner_kind = inner_action.get("action")
    if not inner_kind:
        return

    for i in range(times):
        log.debug("browser.repeat_iteration", iteration=i, total=times)
        await _execute_action(page, inner_action, inner_kind)
        await asyncio.sleep(action.get("delay", 1.0))


_CONTENT_NAVIGATING_MARKER = "page is navigating and changing the content"


async def safe_content(page: Page) -> str:
    """
    Safely retrieve page content, retrying on navigation race conditions.
    """
    for attempt in range(2):
        try:
            from job_ftch.infrastructure.sources.source_deadline import (
                await_with_source_deadline,
                sleep_with_source_deadline,
            )

            return await await_with_source_deadline(page.content())
        except Exception as exc:
            if _CONTENT_NAVIGATING_MARKER in str(exc) and attempt == 0:
                log.debug("browser.content_navigating_retry")
                await sleep_with_source_deadline(0.5)
                continue
            raise
    return await await_with_source_deadline(page.content())  # Fallback


async def scroll_to_bottom(
    page: Page,
    *,
    max_scrolls: int = 50,
    scroll_pause_seconds: float = 0.5,
    pixel_step: int = 2000,
    return_to_top: bool = True,
) -> None:
    """
    Scroll down the page until no new height is gained or max_scrolls is reached.
    Useful for infinite-scroll / lazy-loading lists.
    """
    last_height = 0
    for _ in range(max_scrolls):
        from job_ftch.infrastructure.sources.source_deadline import (
            await_with_source_deadline,
            sleep_with_source_deadline,
        )

        current_height = await await_with_source_deadline(
            page.evaluate("() => document.body.scrollHeight")
        )
        if current_height == last_height:
            break
        last_height = current_height
        await await_with_source_deadline(page.evaluate(f"() => window.scrollBy(0, {pixel_step})"))
        await sleep_with_source_deadline(scroll_pause_seconds)
    # One final wait to let any late renders settle.
    await sleep_with_source_deadline(scroll_pause_seconds)
    if return_to_top:
        await await_with_source_deadline(page.evaluate("() => window.scrollTo(0, 0)"))


# JS executed inside the Playwright page via ``page.evaluate``. Returns
# ``{status, headers, text}`` so HTTP-level errors (which JS ``fetch``
# doesn't reject on) are observable on the Python side. ``headers`` is
# materialized into a plain object with lower-cased keys (the ``Headers``
# object is iterable but not directly serializable across the
# page-evaluate bridge) so callers can do a uniform case-insensitive lookup.
_BROWSER_FETCH_JS = (
    "async (url) => { "
    "const r = await fetch(url); "
    "const headers = {}; "
    "if (typeof r.headers.forEach === 'function') { "
    "  r.headers.forEach((v, k) => { headers[String(k).toLowerCase()] = v; }); "
    "} else if (typeof r.headers.entries === 'function') { "
    "  for (const [k, v] of r.headers.entries()) { headers[String(k).toLowerCase()] = v; } "
    "} "
    "return { status: r.status, headers: headers, text: await r.text() }; "
    "}"
)


async def fetch_in_page(page: Page, url: str) -> dict[str, Any]:
    """Fetch *url* from inside the browser page's JS context.

    Running the fetch via ``page.evaluate`` means the request inherits the
    browser's own TLS/JA3 fingerprint and cookie jar instead of a separate
    httpx connection, which some anti-bot layers otherwise distinguish from
    the page's own navigation traffic.

    Args:
        page: An open Playwright page (already navigated to a same-origin
            or CORS-permitting context, as appropriate for *url*).
        url: The absolute URL to fetch.

    Returns:
        A dict with keys ``status`` (int), ``headers`` (dict[str, str] with
        lower-cased keys), and ``text`` (str, the response body). Note this
        reflects whatever the in-page ``fetch()`` call returns; JS ``fetch``
        does not raise on non-2xx statuses, so callers must check ``status``
        themselves.
    """
    from job_ftch.infrastructure.network.ssrf_guard import check_ssrf

    await check_ssrf(url)

    try:
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        result = await await_with_source_deadline(page.evaluate(_BROWSER_FETCH_JS, url))
    except TypeError:
        result = await await_with_source_deadline(
            page.evaluate(f"({_BROWSER_FETCH_JS})({json.dumps(url)})")
        )
    return dict(result)
