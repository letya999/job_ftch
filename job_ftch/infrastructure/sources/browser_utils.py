from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import Browser, BrowserContext, Page, Playwright

log = structlog.get_logger()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_WAIT = "networkidle"
DEFAULT_WAIT_FALLBACK = "domcontentloaded"
DEFAULT_TIMEOUT = 30_000
CONTEXT_TIMEOUT = 120_000

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
    '#didomi-host',
    '.cc-banner',
    '.cc-window',
    '.cc-revoke',
    '.cc-type-info',
)

@asynccontextmanager
async def open_page(
    pw: Playwright,
    config: dict[str, Any],
    *,
    use_proxy: bool = False,
) -> AsyncIterator[Page]:
    """
    High-level entry point to open a Playwright page with optional stealth and proxy.
    """
    persistent = config.get("persistent_context", False)
    if persistent:
        async with _open_persistent_page(pw, config, use_proxy=use_proxy) as p:
            yield p
        return

    # Standard non-persistent context
    headless = config.get("headless", True)
    channel = config.get("channel")
    stealth = config.get("stealth", True)
    
    launch_args = []
    if stealth:
        launch_args.append("--disable-blink-features=AutomationControlled")
    
    if config.get("disable_http2"):
        launch_args.append("--disable-http2")

    browser: Browser = await pw.chromium.launch(
        headless=headless,
        channel=channel,
        args=launch_args,
    )
    
    proxy_config = None
    if use_proxy:
        proxy_url = os.environ.get("JOB_FTCH_HTTP_PROXY")
        if proxy_url:
            from playwright.async_api import ProxySettings
            proxy_config = ProxySettings(server=proxy_url)

    context: BrowserContext = await browser.new_context(
        user_agent=config.get("user_agent", DEFAULT_USER_AGENT),
        viewport=config.get("viewport", {"width": 1440, "height": 900}),
        locale=config.get("locale", "en-US"),
        ignore_https_errors=config.get("skip_ssl", False),
        proxy=proxy_config,
    )
    
    context.set_default_timeout(config.get("timeout", DEFAULT_TIMEOUT))
    
    if config.get("cookies"):
        await context.add_cookies(config["cookies"])

    page: Page = await context.new_page()
    
    try:
        if config.get("warmup_url"):
            await page.goto(config["warmup_url"])
        yield page
    finally:
        await browser.close()

@asynccontextmanager
async def _open_persistent_page(
    pw: Playwright,
    config: dict[str, Any],
    *,
    use_proxy: bool = False,
) -> AsyncIterator[Page]:
    """
    Opens a page using launch_persistent_context for better stealth.
    """
    import tempfile
    
    user_data_dir = tempfile.mkdtemp(prefix="pw_profile_")
    headless = config.get("headless", True)
    channel = config.get("channel", "chrome")  # Default to real chrome for persistent
    stealth = config.get("stealth", True)
    
    args = []
    if stealth:
        args.append("--disable-blink-features=AutomationControlled")
    if config.get("disable_http2"):
        args.append("--disable-http2")
        
    proxy_config = None
    if use_proxy:
        proxy_url = os.environ.get("JOB_FTCH_HTTP_PROXY")
        if proxy_url:
            from playwright.async_api import ProxySettings
            proxy_config = ProxySettings(server=proxy_url)

    context: BrowserContext = await pw.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        channel=channel,
        args=args,
        user_agent=config.get("user_agent", DEFAULT_USER_AGENT),
        viewport=config.get("viewport", {"width": 1440, "height": 900}),
        locale=config.get("locale", "en-US"),
        ignore_https_errors=config.get("skip_ssl", False),
        proxy=proxy_config,
        timeout=CONTEXT_TIMEOUT,
    )
    
    context.set_default_timeout(config.get("timeout", DEFAULT_TIMEOUT))
    
    if config.get("cookies"):
        await context.add_cookies(config["cookies"])

    page = context.pages[0] if context.pages else await context.new_page()
    
    try:
        if config.get("warmup_url"):
            await page.goto(config["warmup_url"])
        yield page
    finally:
        await context.close()
        # Clean up temp dir if possible (might fail on windows if files locked)
        import shutil

        with suppress(Exception):
            shutil.rmtree(user_data_dir, ignore_errors=True)

async def navigate(page: Page, url: str, config: dict[str, Any]) -> None:
    """
    Navigate to a URL with fallback wait strategies.
    """
    wait = config.get("wait", DEFAULT_WAIT)
    wait_fallback = config.get("wait_fallback", DEFAULT_WAIT_FALLBACK)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    
    try:
        await page.goto(url, wait_until=wait, timeout=timeout)
    except Exception as exc:
        if wait_fallback and wait != wait_fallback:
            log.warning("browser.navigate_fallback", url=url, error=str(exc), fallback=wait_fallback)
            await page.goto(url, wait_until=wait_fallback, timeout=timeout)
        else:
            raise

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
            await page.evaluate(f"() => document.querySelectorAll('{selector}').forEach(el => el.remove())")
    
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
            return await page.content()
        except Exception as exc:
            if _CONTENT_NAVIGATING_MARKER in str(exc) and attempt == 0:
                log.debug("browser.content_navigating_retry")
                await asyncio.sleep(0.5)
                continue
            raise
    return await page.content()  # Fallback
