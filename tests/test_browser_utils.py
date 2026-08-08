from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from job_ftch.infrastructure.sources.browser_utils import (
    _launch_browser_with_recovery,
    _patchright_inner_send_cancellation_safe,
    _patchright_route_handle_cancellation_safe,
    _unroute_page_before_close,
    open_page,
    scroll_to_bottom,
)


class _FakePage:
    def __init__(self) -> None:
        self.unroute_behaviors: list[str] = []

    async def goto(self, url: str) -> None:
        self.last_goto = url

    async def unroute_all(self, *, behavior: str) -> None:
        self.unroute_behaviors.append(behavior)


@pytest.mark.asyncio
async def test_patchright_callback_is_cancelled_with_the_waiting_task() -> None:
    loop = asyncio.get_running_loop()
    callback_future: asyncio.Future[object] = loop.create_future()
    transport_error: asyncio.Future[object] = loop.create_future()

    class _Callback:
        future = callback_future

    class _Connection:
        _error = None
        _transport = SimpleNamespace(on_error_future=transport_error)

        def _send_message_to_server(self, *args: object) -> _Callback:
            del args
            return _Callback()

    channel = SimpleNamespace(_connection=_Connection(), _object=object())
    task = asyncio.create_task(
        _patchright_inner_send_cancellation_safe(channel, "goto", None, None, False)
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert callback_future.cancelled()


@pytest.mark.asyncio
async def test_patchright_route_callback_does_not_complete_cancelled_future() -> None:
    class RouteHandler:
        _ignore_exception = False
        _is_sync = False

        def __init__(self) -> None:
            self._active_invocations: set[object] = set()

        async def _handle_internal(self, route: object) -> bool:
            del route
            invocation = next(iter(self._active_invocations))
            invocation.complete.cancel()  # type: ignore[attr-defined]
            return True

    handler = RouteHandler()
    assert await _patchright_route_handle_cancellation_safe(handler, object()) is True
    assert handler._active_invocations == set()


@pytest.mark.asyncio
async def test_unroute_drain_suppresses_cancellation_to_finish_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    behaviors: list[str] = []

    class Page:
        async def unroute_all(self, *, behavior: str) -> None:
            behaviors.append(behavior)
            started.set()
            await release.wait()

    task = asyncio.create_task(_unroute_page_before_close(Page()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    await task
    assert behaviors == ["ignoreErrors"]


@pytest.mark.asyncio
async def test_unroute_drain_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.infrastructure.sources import browser_utils

    monkeypatch.setattr(browser_utils, "_BROWSER_CLEANUP_TIMEOUT_SECONDS", 0.01)

    class Page:
        async def unroute_all(self, *, behavior: str) -> None:
            assert behavior == "ignoreErrors"
            await asyncio.Event().wait()

    await _unroute_page_before_close(Page())


def test_select_stale_driver_pids_selects_old_childless_browsers() -> None:
    from job_ftch.infrastructure.sources.browser_utils import _select_stale_driver_pids

    # (pid, ppid, age_seconds, is_browser)
    snapshot = [
        (100, 42, 250.0, True),  # stale + browser + childless -> reaped
        (101, 42, 250.0, True),  # browser but parent of 102 -> live, kept
        (102, 101, 249.0, True),  # child of 101 -> not orphaned, kept
        (103, 42, 20.0, True),  # too young -> kept
        (104, 42, 250.0, False),  # not a browser -> kept
    ]

    assert _select_stale_driver_pids(snapshot, min_age_seconds=180) == [100]


@pytest.mark.asyncio
async def test_launch_browser_with_recovery_retries_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.infrastructure.sources import browser_utils

    calls = 0
    reaped = False

    def reap() -> None:
        nonlocal reaped
        reaped = True

    async def launch() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Failed to connect to browser")
        return object()

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(browser_utils, "reap_stale_browser_drivers", reap)
    monkeypatch.setattr(browser_utils.asyncio, "sleep", no_sleep)

    result = await _launch_browser_with_recovery(launch, label="test.launch")

    assert result is not None
    assert calls == 2
    assert reaped is True


@pytest.mark.asyncio
async def test_scroll_to_bottom_can_preserve_progress() -> None:
    calls: list[str] = []

    class _ScrollingPage:
        async def evaluate(self, expression: str) -> int | None:
            calls.append(expression)
            if "scrollHeight" in expression:
                return 4000
            return None

    await scroll_to_bottom(
        _ScrollingPage(),  # type: ignore[arg-type]
        max_scrolls=1,
        scroll_pause_seconds=0,
        pixel_step=3000,
        return_to_top=False,
    )

    assert "() => window.scrollBy(0, 3000)" in calls
    assert "() => window.scrollTo(0, 0)" not in calls


class _FakeContext:
    def __init__(self) -> None:
        self.default_timeout = None
        self.cookies = None
        self.page = _FakePage()

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    async def add_cookies(self, cookies: list[dict[str, object]]) -> None:
        self.cookies = cookies

    async def new_page(self) -> _FakePage:
        return self.page


class _FakeBrowser:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured
        self.context = _FakeContext()
        self.closed = False
        self.version = "150.0.0.0"

    async def new_context(self, **kwargs: object) -> _FakeContext:
        self.captured["context_kwargs"] = kwargs
        return self.context

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured
        self.browser = _FakeBrowser(captured)

    async def launch(self, **kwargs: object) -> _FakeBrowser:
        self.captured["launch_kwargs"] = kwargs
        return self.browser


class _FakePlaywright:
    def __init__(self, captured: dict[str, object]) -> None:
        self.chromium = _FakeChromium(captured)


class _FakeAsyncPlaywrightContext:
    def __init__(self, captured: dict[str, object]) -> None:
        self._playwright = _FakePlaywright(captured)

    async def __aenter__(self) -> _FakePlaywright:
        return self._playwright

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        return None


class _FakeBypass:
    def __init__(self) -> None:
        self.page_calls = 0

    def apply_browser_args(self, kwargs: dict[str, object]) -> dict[str, object]:
        kwargs["proxy"] = {"server": "http://proxy.local:8080"}
        return kwargs

    async def apply_page(self, page: object) -> None:
        del page
        self.page_calls += 1


@pytest.mark.asyncio
async def test_open_page_applies_bypass_only_to_launch_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    bypass = _FakeBypass()

    def fake_settings() -> SimpleNamespace:
        return SimpleNamespace(
            browser_default_timeout_ms=1234,
            browser_context_timeout_ms=4321,
            browser_channel="",
            browser_headless=True,
            career_site_browser_concurrency=4,
        )

    monkeypatch.setattr("job_ftch.config.get_settings", fake_settings)
    monkeypatch.setattr("job_ftch.infrastructure.sources.browser_utils.get_settings", fake_settings)
    fake_async_api = SimpleNamespace(async_playwright=lambda: _FakeAsyncPlaywrightContext(captured))
    monkeypatch.setitem(sys.modules, "patchright.async_api", fake_async_api)

    async with open_page(
        {"headless": True, "locale": "ru-RU"},
        bypass_strategy=bypass,
    ) as page:
        assert isinstance(page, _FakePage)

    launch_kwargs = captured["launch_kwargs"]
    context_kwargs = captured["context_kwargs"]
    assert isinstance(launch_kwargs, dict)
    assert isinstance(context_kwargs, dict)
    assert "channel" not in launch_kwargs
    assert launch_kwargs["proxy"] == {"server": "http://proxy.local:8080"}
    assert "proxy" not in context_kwargs
    assert "args" not in context_kwargs
    assert bypass.page_calls == 1
    assert page.unroute_behaviors == ["ignoreErrors"]


@pytest.mark.asyncio
async def test_open_page_reprojects_runtime_aligned_persona_ua(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_settings() -> SimpleNamespace:
        return SimpleNamespace(
            browser_default_timeout_ms=1234,
            browser_context_timeout_ms=4321,
            browser_channel="",
            browser_headless=True,
            career_site_browser_concurrency=4,
        )

    class _Persona:
        ua = "Mozilla/5.0 Chrome/145.0.0.0"

    class _BypassContext:
        persona = _Persona()

        def context_kwargs(self) -> dict[str, object]:
            return {"user_agent": self.persona.ua}

        def align_browser_runtime(self, browser_family: str, reported_version: str) -> None:
            assert browser_family == "chromium"
            assert reported_version == "150.0.0.0"
            self.persona.ua = "Mozilla/5.0 Chrome/150.0.0.0"

        async def on_page(self, page: object) -> None:
            del page

    monkeypatch.setattr("job_ftch.config.get_settings", fake_settings)
    monkeypatch.setattr("job_ftch.infrastructure.sources.browser_utils.get_settings", fake_settings)
    fake_async_api = SimpleNamespace(async_playwright=lambda: _FakeAsyncPlaywrightContext(captured))
    monkeypatch.setitem(sys.modules, "patchright.async_api", fake_async_api)

    async with open_page(
        {
            "headless": True,
            "user_agent": "Mozilla/5.0 Chrome/145.0.0.0",
            "_bypass_context": _BypassContext(),
        },
    ):
        pass

    context_kwargs = captured["context_kwargs"]
    assert isinstance(context_kwargs, dict)
    assert context_kwargs["user_agent"] == "Mozilla/5.0 Chrome/150.0.0.0"


@pytest.mark.asyncio
async def test_open_page_delegates_to_custom_bypass() -> None:
    sentinel_page = MagicMock()

    class _CustomBypass:
        async def apply_http(self, client: object) -> object:
            return client

        def apply_browser_args(self, kwargs: dict[str, object]) -> dict[str, object]:
            return kwargs

        async def apply_page(self, page: object) -> None:
            del page

        @asynccontextmanager
        async def open_page(
            self,
            config: dict[str, object],
            *,
            use_proxy: bool = False,
        ) -> object:
            assert config["headless"] is True
            assert use_proxy is True
            yield sentinel_page

    async with open_page(
        {"headless": True},
        use_proxy=True,
        bypass_strategy=_CustomBypass(),
    ) as page:
        assert page is sentinel_page


@pytest.mark.asyncio
async def test_open_page_caps_concurrent_browser_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    import job_ftch.infrastructure.sources.browser_utils as browser_utils

    slots = asyncio.Semaphore(2)

    @asynccontextmanager
    async def _browser_slot(capacity: int):
        assert capacity > 0
        async with slots:
            yield

    monkeypatch.setattr(browser_utils, "browser_slot", _browser_slot)
    entered = 0
    peak = 0
    release = asyncio.Event()

    class _CustomBypass:
        async def apply_http(self, client: object) -> object:
            return client

        def apply_browser_args(self, kwargs: dict[str, object]) -> dict[str, object]:
            return kwargs

        async def apply_page(self, page: object) -> None:
            del page

        @asynccontextmanager
        async def open_page(self, config: dict[str, object], *, use_proxy: bool = False) -> object:
            del config, use_proxy
            yield MagicMock()

    async def _hold_page() -> None:
        nonlocal entered, peak
        async with open_page({"headless": True}, bypass_strategy=_CustomBypass()):
            entered += 1
            peak = max(peak, entered)
            await release.wait()
            entered -= 1

    tasks = [asyncio.create_task(_hold_page()) for _ in range(3)]
    for _ in range(20):
        if entered == 2:
            break
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)

    assert peak == 2
