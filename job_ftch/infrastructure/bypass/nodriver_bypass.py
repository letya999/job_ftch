from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.config import get_settings

try:
    import nodriver
    from nodriver import cdp

    _NODRIVER_AVAILABLE = True
except ImportError:
    nodriver = None
    cdp = None
    _NODRIVER_AVAILABLE = False


class _NodriverResponse:
    def __init__(self, *, url: str, status: int, headers: dict[str, str], body: str) -> None:
        self.url = url
        self.status = status
        self._headers = headers
        self._body = body
        self.request: Any = None

    async def all_headers(self) -> dict[str, str]:
        return dict(self._headers)

    async def json(self) -> Any:
        return json.loads(self._body)

    async def text(self) -> str:
        return self._body


def _default_browser_executable_path() -> str | None:
    configured = os.environ.get("JOB_FTCH_NODRIVER_BROWSER_EXECUTABLE_PATH", "").strip()
    if configured:
        return configured
    browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not browsers_root:
        return None
    root = Path(browsers_root)
    if not root.exists():
        return None
    candidates = [
        *root.rglob("chrome"),
        *root.rglob("chromium"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _cookie_to_mapping(cookie: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source, target in (
        ("name", "name"),
        ("value", "value"),
        ("domain", "domain"),
        ("path", "path"),
        ("expires", "expires"),
        ("secure", "secure"),
        ("http_only", "httpOnly"),
        ("same_site", "sameSite"),
    ):
        value = getattr(cookie, source, None)
        if value is not None:
            result[target] = value
    return result


class _NodriverPageAdapter:
    def __init__(
        self,
        tab: Any,
        *,
        cookies: list[dict[str, Any]] | None = None,
        viewport: dict[str, Any] | None = None,
        timezone_id: str | None = None,
    ) -> None:
        self._tab = tab
        self._cookies = list(cookies or [])
        self._viewport = viewport
        self._timezone_id = timezone_id
        self._init_scripts: list[str] = []
        self._network_enable_task: asyncio.Task[None] | None = None
        self._cookie_origins: set[str] = set()

    async def initialize(self) -> None:
        if self._timezone_id and cdp is not None:
            with suppress(Exception):
                await self._tab.send(cdp.emulation.set_timezone_override(self._timezone_id))
        if not isinstance(self._viewport, dict):
            return
        width = int(self._viewport.get("width", 1440) or 1440)
        height = int(self._viewport.get("height", 900) or 900)
        try:
            await self._tab.set_window_size(0, 0, width, height)
        except Exception:
            return

    @property
    def url(self) -> str:
        return str(getattr(self._tab, "url", "") or "")

    @property
    def viewport_size(self) -> dict[str, int]:
        width = int((self._viewport or {}).get("width", 1440) or 1440)
        height = int((self._viewport or {}).get("height", 900) or 900)
        return {"width": width, "height": height}

    def on(self, event: str, handler: Any) -> None:
        if event != "response":
            return
        if cdp is None:
            return
        self._network_enable_task = asyncio.create_task(self._enable_response_events(handler))

    async def _enable_response_events(self, handler: Any) -> None:
        await self._tab.send(cdp.network.enable())

        self._requests: dict[str, Any] = {}

        async def _handle_request(event: Any, tab: Any | None = None) -> None:
            del tab
            req = getattr(event, "request", None)
            if req:
                self._requests[event.request_id] = req

        async def _handle_event(event: Any, tab: Any | None = None) -> None:
            del tab
            response_obj = getattr(event, "response", None)
            if response_obj is None:
                return
            headers = _normalize_headers(getattr(response_obj, "headers", {}))
            body = ""
            try:
                body, is_base64 = await self._tab.send(
                    cdp.network.get_response_body(event.request_id)
                )
                if is_base64:
                    body = base64.b64decode(body).decode("utf-8", errors="ignore")
            except Exception:
                body = ""

            req_obj = self._requests.get(event.request_id)
            req_method = getattr(req_obj, "method", "GET") if req_obj else "GET"
            req_headers = getattr(req_obj, "headers", {}) if req_obj else {}
            req_post_data = (
                getattr(req_obj, "postData", None)
                if req_obj
                else getattr(req_obj, "hasPostData", False)
            )  # we can't easily get postData if it's omitted

            wrapped = _NodriverResponse(
                url=str(getattr(response_obj, "url", "") or ""),
                status=int(getattr(response_obj, "status", 0) or 0),
                headers=headers,
                body=body,
            )

            # Monkey-patch request details onto the response for api_sniffer
            class _FakeRequest:
                def __init__(self, method: str, headers: dict[str, str], post_data: Any) -> None:
                    self.method = method
                    self.headers = headers
                    self.post_data = post_data

                def all_headers(self) -> dict[str, str]:
                    return self.headers

            wrapped.request = _FakeRequest(
                req_method, _normalize_headers(req_headers), req_post_data
            )

            result = handler(wrapped)
            if inspect.isawaitable(result):
                await result

        self._tab.add_handler(cdp.network.RequestWillBeSent, _handle_request)
        self._tab.add_handler(cdp.network.ResponseReceived, _handle_event)

    async def goto(
        self, url: str, wait_until: str | None = None, timeout: int | None = None
    ) -> None:
        del wait_until
        if self._network_enable_task is not None:
            await self._network_enable_task
        await self._prime_cookies(url)
        if cdp is not None:
            nav = self._tab.send(cdp.page.navigate(url))
            if timeout is not None:
                await asyncio.wait_for(nav, timeout / 1000)
            else:
                await nav
        else:
            nav = self._tab.get(url)
            if timeout is not None:
                self._tab = await asyncio.wait_for(nav, timeout / 1000)
            else:
                self._tab = await nav
            for script in self._init_scripts:
                await self._install_init_script(script)
        await self.wait_for_load_state("domcontentloaded", timeout=timeout)
        return None

    async def add_init_script(self, script: str) -> None:
        self._init_scripts.append(script)
        await self._install_init_script(script)

    async def _install_init_script(self, script: str) -> None:
        if cdp is None:
            await self.evaluate(script)
            return
        await self._tab.send(
            cdp.page.add_script_to_evaluate_on_new_document(script, run_immediately=True)
        )

    async def content(self) -> str:
        return str(await self._tab.get_content())

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        final_expression = expression
        if arg is not None:
            final_expression = f"({expression})({json.dumps(arg)})"
        result = await self._tab.evaluate(
            final_expression,
            await_promise=True,
            return_by_value=True,
        )
        return _materialize_runtime_value(result)

    async def click(self, selector: str, timeout: int | None = None) -> None:
        del timeout
        await self.evaluate(
            "(sel) => { const el = document.querySelector(sel); if (el) { el.click(); return true; } return false; }",
            selector,
        )

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        await asyncio.sleep(timeout_ms / 1000)

    async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        ready_states = {
            "domcontentloaded": {"interactive", "complete"},
            "networkidle": {"complete"},
        }
        accepted = ready_states.get(state, {"complete"})
        deadline = None if timeout is None else asyncio.get_running_loop().time() + (timeout / 1000)
        while True:
            current = await self.evaluate("document.readyState")
            if current in accepted:
                if state == "networkidle":
                    await asyncio.sleep(0.5)
                return
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"nodriver wait_for_load_state timed out for state={state}")
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        await self._tab.close()

    async def export_cookies(self) -> list[dict[str, Any]]:
        if cdp is None:
            return []
        try:
            cookies = await self._tab.send(cdp.network.get_all_cookies())
        except Exception:
            return []
        return [_cookie_to_mapping(cookie) for cookie in cookies]

    async def _prime_cookies(self, url: str) -> None:
        if cdp is None or not self._cookies:
            return
        parsed = urlparse(url)
        origin_key = parsed.netloc.lower()
        if not origin_key or origin_key in self._cookie_origins:
            return
        for cookie in self._cookies:
            domain = str(cookie.get("domain", "") or "").lstrip(".").lower()
            if domain and not (origin_key == domain or origin_key.endswith(f".{domain}")):
                continue
            payload: dict[str, Any] = {
                "name": str(cookie.get("name", "")),
                "value": str(cookie.get("value", "")),
                "domain": domain or origin_key,
                "path": str(cookie.get("path", "/") or "/"),
                "secure": bool(cookie.get("secure", False)),
            }
            if "httpOnly" in cookie:
                payload["http_only"] = bool(cookie["httpOnly"])
            if "expires" in cookie and cookie["expires"] is not None:
                payload["expires"] = cookie["expires"]
            try:
                await self._tab.send(cdp.network.set_cookie(**payload))
            except Exception:
                continue
        self._cookie_origins.add(origin_key)


class NodriverBypass:
    """Browser-tier bypass backed by nodriver/CDP rather than Playwright."""

    def __init__(
        self,
        *,
        browser_executable_path: str | None = None,
        browser_args: list[str] | None = None,
        lang: str | None = None,
    ) -> None:
        self._browser_executable_path = browser_executable_path
        self._browser_args = browser_args or []
        self._lang = lang

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        del page
        return None

    @asynccontextmanager
    async def open_page(self, config: dict[str, Any], *, use_proxy: bool = False) -> Any:
        if nodriver is None:
            raise ImportError(
                "nodriver bypass requires the 'browser' extra with nodriver installed."
            )

        browser_args = list(self._browser_args)
        if config.get("disable_http2"):
            browser_args.append("--disable-http2")
        if config.get("skip_ssl"):
            browser_args.append("--ignore-certificate-errors")
        if config.get("user_agent"):
            browser_args.append(f"--user-agent={config['user_agent']}")
        cdp_mitigation_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
            "--disable-features=TranslateUI",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
        ]
        for arg in cdp_mitigation_args:
            if arg not in browser_args:
                browser_args.append(arg)

        viewport = config.get("viewport")
        if isinstance(viewport, dict):
            width = int(viewport.get("width", 1440) or 1440)
            height = int(viewport.get("height", 900) or 900)
            browser_args.append(f"--window-size={width},{height}")

        user_data_dir: str | None = None
        owns_profile_dir = False
        if config.get("persistent_context"):
            shared_profile_dir = config.get("_profile_dir")
            user_data_dir = (
                str(shared_profile_dir)
                if shared_profile_dir
                else tempfile.mkdtemp(prefix="nodriver_profile_")
            )
            owns_profile_dir = not bool(shared_profile_dir)

        browser = await nodriver.start(
            headless=bool(config.get("headless", True)),
            user_data_dir=user_data_dir,
            browser_executable_path=self._browser_executable_path,
            browser_args=browser_args,
            lang=self._lang or str(config.get("locale", "en-US")),
        )
        proxy_url = (
            config.get("_proxy_url") or os.environ.get("JOB_FTCH_HTTP_PROXY") if use_proxy else None
        )
        if proxy_url:
            tab = await browser.create_context(
                url="about:blank",
                proxy_server=proxy_url,
                proxy_bypass_list=["localhost"],
            )
        else:
            tab = await browser.get("about:blank")
        page = _NodriverPageAdapter(
            tab,
            cookies=config.get("cookies"),
            viewport=viewport if isinstance(viewport, dict) else None,
            timezone_id=str(config.get("timezone_id") or "") or None,
        )
        await page.initialize()
        try:
            if config.get("warmup_url"):
                await page.goto(
                    str(config["warmup_url"]),
                    timeout=config.get("timeout", get_settings().browser_default_timeout_ms),
                )
            yield page
        finally:
            stop_result = browser.stop()
            if inspect.isawaitable(stop_result):
                await stop_result
            if user_data_dir is not None and owns_profile_dir:
                shutil.rmtree(user_data_dir, ignore_errors=True)


def _create_nodriver(bypass_config: dict[str, Any] | None = None) -> NodriverBypass:
    config = bypass_config or {}
    raw_args = config.get("browser_args", [])
    browser_args = [str(arg) for arg in raw_args] if isinstance(raw_args, list) else []
    bypass = NodriverBypass(
        browser_executable_path=config.get("browser_executable_path")
        or _default_browser_executable_path(),
        browser_args=browser_args,
        lang=config.get("lang"),
    )
    try:
        from job_ftch.infrastructure.bypass.fingerprint_profile import patch_nodriver_bypass

        patch_nodriver_bypass(bypass)
    except ImportError:
        pass
    return bypass


def _normalize_headers(raw_headers: Any) -> dict[str, str]:
    if hasattr(raw_headers, "items"):
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}
    if hasattr(raw_headers, "to_json"):
        data = raw_headers.to_json()
        if isinstance(data, dict):
            return {str(key).lower(): str(value) for key, value in data.items()}
    if isinstance(raw_headers, dict):
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}
    return {}


def _materialize_runtime_value(raw: Any) -> Any:
    if raw is None or isinstance(raw, (str, int, float, bool)):
        return raw
    deep_value = getattr(raw, "deep_serialized_value", None)
    if deep_value is not None:
        return _materialize_deep_serialized_value(deep_value)
    plain_value = getattr(raw, "value", None)
    if plain_value is not None:
        return plain_value
    if isinstance(raw, list):
        return [_materialize_runtime_value(item) for item in raw]
    if isinstance(raw, dict):
        if "type" in raw and "value" in raw and len(raw) <= 3:
            value_type = raw.get("type")
            value = raw.get("value")
            if value_type == "object" and isinstance(value, list):
                materialized: dict[str, Any] = {}
                for item in value:
                    if isinstance(item, list | tuple) and len(item) == 2:
                        key, child = item
                        materialized[str(key)] = _materialize_runtime_value(child)
                return materialized
            if value_type == "array" and isinstance(value, list):
                return [_materialize_runtime_value(item) for item in value]
            return _materialize_runtime_value(value)
        return {key: _materialize_runtime_value(value) for key, value in raw.items()}
    return raw


def _materialize_deep_serialized_value(raw: Any) -> Any:
    value_type = getattr(raw, "type_", getattr(raw, "type", None))
    value = getattr(raw, "value", None)
    if value_type == "object" and isinstance(value, list):
        materialized: dict[str, Any] = {}
        for item in value:
            if isinstance(item, list | tuple) and len(item) == 2:
                key, child = item
                materialized[str(key)] = _materialize_runtime_value(child)
        return materialized
    if value_type == "array" and isinstance(value, list):
        return [_materialize_runtime_value(item) for item in value]
    return value


if _NODRIVER_AVAILABLE:
    register_bypass(
        "nodriver",
        capability=BypassCapability(
            cost=30,
            browser_family="chromium_cdp",
            owns_session=True,
            challenge_actions=frozenset({"cdp_checkbox", "cloudflare_challenge"}),
            legal_gate="adr_073",
            min_remaining_seconds=10.0,
        ),
    )(_create_nodriver)
