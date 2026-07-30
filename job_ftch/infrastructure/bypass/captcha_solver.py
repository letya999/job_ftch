"""CAPTCHA challenge solver — composable decorator for BypassStrategy.

Supports three solving modes:
1. Browser-wait: open the challenge page in a real browser, wait for the
   anti-bot JS to resolve (Cloudflare Turnstile auto-solves with a good
   fingerprint), then harvest clearance cookies. Free, no API key needed.
2. Token extraction: for hCaptcha/reCAPTCHA embedded in career sites,
   wait for the challenge widget to appear and extract the response token
   via JS evaluation. Free.
3. External API: delegate to NopeCHA (free tier, recurring credits every 23h),
   Capsolver, 2captcha, or anticaptcha when browser-wait fails. Requires an
   API key via the provider's env var (see `_create_captcha_solver`).

The solver is a composable decorator, not a standalone escalation tier.
It wraps apply_page() to add challenge detection and cookie harvesting
on top of whatever browser tier is active.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import structlog

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.infrastructure.bypass.captcha_models import (
    CaptchaFailureReason,
    CaptchaSolveResult,
)
from job_ftch.infrastructure.bypass.captcha_providers import (
    extract_sitekey,
    resolve_captcha_provider,
)
from job_ftch.infrastructure.sources.source_deadline import (
    remaining_source_seconds,
    sleep_with_source_deadline,
)

logger = structlog.get_logger("job_ftch.bypass.captcha")

_CF_CHALLENGE_SELECTORS = (
    "#challenge-running",
    "#challenge-stage",
    "#turnstile-wrapper",
    '[class*="cf-turnstile"]',
    '[data-sitekey*="0x"]',
    "#cf-challenge",
)

_HCAPTCHA_SELECTORS = (
    ".h-captcha",
    "#h-captcha",
    "[data-sitekey][data-callback]",
    "iframe[src*='hcaptcha.com']",
)

_RECAPTCHA_SELECTORS = (
    ".g-recaptcha",
    "#g-recaptcha",
    "iframe[src*='recaptcha']",
    "[data-sitekey]",
)

_CLEARANCE_COOKIE_NAMES = (
    "cf_clearance",
    "cf_bm",
    "__cfduid",
    "dd_cookie",
    "_px3",
    "datadome",
)


@dataclass(slots=True)
class _CookieCache:
    cookies: dict[str, str]
    harvested_at: float
    ttl_seconds: float = 900.0

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.harvested_at) > self.ttl_seconds


class CaptchaSolverBypass:
    """Composable CAPTCHA solver decorator for browser tiers.

    Wraps apply_page() to detect challenge pages, wait for resolution,
    and harvest clearance cookies. Caches cookies per-domain to avoid
    re-solving on subsequent visits.
    """

    DEFAULT_WAIT_SECONDS: float = 15.0
    MAX_WAIT_SECONDS: float = 45.0
    POLL_INTERVAL_SECONDS: float = 1.0

    def __init__(
        self,
        provider: str = "browser_wait",
        api_key: str = "",
        *,
        wait_seconds: float | None = None,
        max_attempts: int = 2,
        max_paid_attempts: int = 1,
        min_provider_seconds: float = 10.0,
        proxy_url: str = "",
        enabled_providers: frozenset[str] | None = None,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        # browser_wait is always permitted (free, no external call); the external
        # provider only fires when it appears in the allowlist.
        self._enabled_providers = (
            frozenset(enabled_providers) if enabled_providers is not None else None
        )
        self._wait = wait_seconds or self.DEFAULT_WAIT_SECONDS
        self._cookie_cache: dict[str, _CookieCache] = {}
        self._max_attempts = max(1, max_attempts)
        self._max_paid_attempts = max(0, max_paid_attempts)
        self._attempts = 0
        self._paid_attempts = 0
        self._min_provider_seconds = max(0.0, min_provider_seconds)
        self._paid_lock = asyncio.Lock()
        self._proxy_url = proxy_url

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        try:
            challenge_type = await self._detect_challenge(page)
            if not challenge_type:
                return
            logger.info("captcha_detected", type=challenge_type)
            result = await self.solve(page, challenge_type=challenge_type)
            if result.solved:
                logger.info(
                    "captcha_solved",
                    method=result.method,
                    cookies=len(result.cookies),
                    elapsed=f"{result.elapsed_seconds:.1f}s",
                )
            else:
                logger.warning(
                    "captcha_solve_failed",
                    method=result.method,
                    reason=result.failure_reason or "unknown",
                )
        except Exception as exc:
            logger.debug("captcha_apply_page_error", error=str(exc))

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str = "unknown",
        url: str = "",
    ) -> CaptchaSolveResult:
        start = time.monotonic()

        domain = ""
        if url:
            from urllib.parse import urlparse

            domain = urlparse(url).netloc.lower()
        elif hasattr(page, "url"):
            from urllib.parse import urlparse

            domain = urlparse(str(page.url)).netloc.lower()

        cached = self._cookie_cache.get(domain)
        if cached and not cached.expired:
            return CaptchaSolveResult(
                solved=True,
                method="cache",
                cookies=cached.cookies,
                elapsed_seconds=0.0,
            )

        if self._attempts >= self._max_attempts:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="solver attempt budget exhausted",
                failure_reason=CaptchaFailureReason.BUDGET_EXHAUSTED,
            )
        self._attempts += 1

        if self._provider == "browser_wait":
            result = await self._solve_browser_wait(page, challenge_type)
        elif self._enabled_providers is not None and self._provider not in self._enabled_providers:
            result = CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error=f"provider '{self._provider}' disabled in settings",
                failure_reason=CaptchaFailureReason.PROVIDER_DISABLED,
            )
        else:
            remaining = remaining_source_seconds()
            if remaining is not None and remaining < self._min_provider_seconds:
                result = CaptchaSolveResult(
                    solved=False,
                    method=self._provider,
                    error="source deadline cannot cover provider timeout",
                    failure_reason=CaptchaFailureReason.DEADLINE_INSUFFICIENT,
                )
            elif self._paid_attempts >= self._max_paid_attempts:
                result = CaptchaSolveResult(
                    solved=False,
                    method=self._provider,
                    error="paid solver budget exhausted",
                    failure_reason=CaptchaFailureReason.BUDGET_EXHAUSTED,
                )
            else:
                async with self._paid_lock:
                    self._paid_attempts += 1
                    result = await self._solve_external_api(page, challenge_type, url)
        if result.solved and result.tokens:
            token = result.tokens.get("captcha_token", "")
            if token and not await self._inject_token(page, challenge_type, token):
                result = CaptchaSolveResult(
                    solved=False,
                    method=result.method,
                    error="provider token could not be injected",
                    failure_reason=CaptchaFailureReason.INJECTION_FAILED,
                )
            elif token and not await self._check_challenge_cleared(page, challenge_type):
                result = CaptchaSolveResult(
                    solved=False,
                    method=result.method,
                    error="challenge remained after token injection",
                    failure_reason=CaptchaFailureReason.VERIFICATION_FAILED,
                )

        result.elapsed_seconds = time.monotonic() - start

        if result.solved and domain and result.cookies:
            self._cookie_cache[domain] = _CookieCache(
                cookies=result.cookies,
                harvested_at=time.monotonic(),
            )

        return result

    async def solve_detected(self, page: Any, *, url: str = "") -> CaptchaSolveResult:
        """Detect and solve on the current page without changing its session."""
        challenge_type = await self._detect_challenge(page)
        if not challenge_type:
            return CaptchaSolveResult(
                solved=False,
                method="none",
                error="no supported challenge detected",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
            )
        return await self.solve(page, challenge_type=challenge_type, url=url)

    async def _detect_challenge(self, page: Any) -> str | None:
        try:
            html = ""
            if hasattr(page, "content"):
                html = await page.content()
            elif hasattr(page, "evaluate"):
                html = str(await page.evaluate("document.documentElement.outerHTML"))

            if not html:
                return None

            html_lower = html.lower()

            if any(
                marker in html_lower
                for marker in (
                    "cf-turnstile",
                    "challenge-running",
                    "challenge-stage",
                    "turnstile-wrapper",
                    'data-sitekey="0x',
                    "data-sitekey='0x",
                )
            ) or ("cloudflare" in html_lower and "challenge" in html_lower):
                return "cloudflare"

            for selector in _HCAPTCHA_SELECTORS:
                clean = selector.lstrip("#.[")
                if clean in html_lower:
                    return "hcaptcha"

            for selector in _RECAPTCHA_SELECTORS:
                clean = selector.lstrip("#.[")
                if clean in html_lower:
                    return "recaptcha"

            if "datadome" in html_lower:
                return "datadome"
            if "perimeterx" in html_lower or "px-captcha" in html_lower:
                return "perimeterx"
            if (
                "captcha-image" in html_lower
                or "captcha_image" in html_lower
                or ("<img" in html_lower and "captcha" in html_lower)
            ):
                return "image"

            return None
        except Exception:
            return None

    async def _solve_browser_wait(
        self,
        page: Any,
        challenge_type: str,
    ) -> CaptchaSolveResult:
        elapsed = 0.0
        poll = self.POLL_INTERVAL_SECONDS

        while elapsed < self._wait:
            await sleep_with_source_deadline(poll)
            elapsed += poll

            cleared = await self._check_challenge_cleared(page, challenge_type)
            if cleared:
                cookies = await self._harvest_cookies(page)
                method = "browser_wait"
                if challenge_type == "cloudflare":
                    method = await self._detect_turnstile_method(page)
                return CaptchaSolveResult(
                    solved=True,
                    method=method,
                    cookies=cookies,
                )

        extended = await self._try_extended_wait(page, challenge_type)
        if extended:
            cookies = await self._harvest_cookies(page)
            return CaptchaSolveResult(
                solved=True,
                method="browser_wait_extended",
                cookies=cookies,
            )

        return CaptchaSolveResult(
            solved=False,
            method="browser_wait",
            error=f"challenge not cleared after {self._wait}s",
            failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
        )

    async def _detect_turnstile_method(self, page: Any) -> str:
        try:
            if hasattr(page, "evaluate"):
                token = await page.evaluate(
                    "(()=>{"
                    "const el=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                    "return el?el.value:'';})()"
                )
                if token:
                    return "turnstile"
        except Exception:
            pass
        return "browser_wait"

    async def _try_extended_wait(
        self,
        page: Any,
        challenge_type: str,
    ) -> bool:
        try:
            if challenge_type == "cloudflare":
                for selector in ("#challenge-stage", "#turnstile-wrapper", "iframe"):
                    try:
                        if hasattr(page, "click"):
                            await page.click(selector, timeout=2000)
                            break
                    except Exception:
                        continue

            elif challenge_type in ("hcaptcha", "recaptcha"):
                for selector in (".h-captcha", ".g-recaptcha", "iframe"):
                    try:
                        if hasattr(page, "click"):
                            await page.click(selector, timeout=2000)
                            break
                    except Exception:
                        continue

            extra_wait = min(self._wait, self.MAX_WAIT_SECONDS - self._wait)
            if extra_wait > 0:
                await sleep_with_source_deadline(extra_wait)
                return await self._check_challenge_cleared(page, challenge_type)
        except Exception:
            pass
        return False

    async def _check_challenge_cleared(self, page: Any, challenge_type: str) -> bool:
        try:
            if hasattr(page, "evaluate"):
                ready_state = await page.evaluate("document.readyState")
                if ready_state not in ("interactive", "complete"):
                    return False

                body_text = str(
                    await page.evaluate(
                        "document.body ? document.body.innerText.substring(0, 500) : ''"
                    )
                )
                body_lower = body_text.lower()

                if challenge_type == "cloudflare":
                    if "checking your browser" in body_lower or "just a moment" in body_lower:
                        return False
                    turnstile_token = await page.evaluate(
                        "(()=>{"
                        "const el=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                        "return el?el.value:'';})()"
                    )
                    if turnstile_token:
                        return True
                    if len(body_text) > 100:
                        return True

                elif challenge_type in ("hcaptcha", "recaptcha"):
                    token = await page.evaluate(
                        "(()=>{"
                        "const el=document.querySelector('[name=\"h-captcha-response\"]')"
                        "||document.querySelector('[name=\"g-recaptcha-response\"]');"
                        "return el?el.value:'';})()"
                    )
                    return bool(token)

                else:
                    return len(body_text) > 100

            return False
        except Exception:
            return False

    async def _harvest_cookies(self, page: Any) -> dict[str, str]:
        cookies: dict[str, str] = {}
        try:
            if hasattr(page, "context") and hasattr(page.context, "cookies"):
                all_cookies = await page.context.cookies()
                for c in all_cookies:
                    name = c.get("name", "")
                    if any(cn in name.lower() for cn in _CLEARANCE_COOKIE_NAMES):
                        cookies[name] = c.get("value", "")

            if not cookies and hasattr(page, "evaluate"):
                doc_cookie = str(await page.evaluate("document.cookie"))
                for part in doc_cookie.split(";"):
                    part = part.strip()
                    if "=" in part:
                        name, _, value = part.partition("=")
                        name = name.strip()
                        if any(cn in name.lower() for cn in _CLEARANCE_COOKIE_NAMES):
                            cookies[name] = value.strip()
        except Exception as exc:
            logger.debug("captcha_cookie_harvest_error", error=str(exc))
        return cookies

    def set_proxy_url(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url

    async def _solve_external_api(
        self,
        page: Any,
        challenge_type: str,
        url: str,
    ) -> CaptchaSolveResult:
        try:
            provider = resolve_captcha_provider(
                self._provider,
                self._api_key,
                proxy_url=self._proxy_url,
            )
        except ValueError:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error=f"unknown provider: {self._provider}",
                failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
            )
        if not self._api_key:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="no api_key configured",
                failure_reason=CaptchaFailureReason.MISSING_CREDENTIAL,
            )

        try:
            result = await provider.solve(page, challenge_type=challenge_type, url=url)
        except TimeoutError:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="provider timed out",
                failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
            )
        except Exception as exc:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error=type(exc).__name__,
                failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
            )
        if not isinstance(result, CaptchaSolveResult):
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="provider returned a malformed result",
                failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
            )
        return result

    async def _extract_sitekey(self, page: Any) -> str:
        """Compatibility wrapper around the provider-neutral detector."""
        return await extract_sitekey(page)

    async def _inject_token(self, page: Any, challenge_type: str, token: str) -> bool:
        """Inject one provider token into the current challenge page."""
        if not token or not hasattr(page, "evaluate"):
            return False
        try:
            return bool(
                await page.evaluate(
                    """({token, challengeType}) => {
                      const selectors = challengeType === 'hcaptcha'
                        ? ['textarea[name="h-captcha-response"]']
                        : ['textarea[name="g-recaptcha-response"]',
                           'input[name="cf-turnstile-response"]'];
                      let changed = false;
                      for (const selector of selectors) {
                        for (const el of document.querySelectorAll(selector)) {
                          el.value = token;
                          el.dispatchEvent(new Event('input', {bubbles: true}));
                          el.dispatchEvent(new Event('change', {bubbles: true}));
                          changed = true;
                        }
                      }
                      return changed;
                    }""",
                    {"token": token, "challengeType": challenge_type},
                )
            )
        except Exception:
            return False

    def get_cached_cookies(self, domain: str) -> dict[str, str]:
        cached = self._cookie_cache.get(domain.lower())
        if cached and not cached.expired:
            return dict(cached.cookies)
        return {}

    def clear_cache(self, domain: str | None = None) -> None:
        if domain:
            self._cookie_cache.pop(domain.lower(), None)
        else:
            self._cookie_cache.clear()


def _create_captcha_solver(
    bypass_config: dict[str, Any] | None = None,
) -> CaptchaSolverBypass:
    config = bypass_config or {}
    from job_ftch.config import get_settings

    settings = get_settings()
    # Per-source bypass_config may override the active provider; otherwise the
    # global setting decides which external provider (if any) backs browser_wait.
    provider = str(config.get("provider", settings.captcha_provider))
    env_keys = {
        "capsolver": "CAPSOLVER_API_KEY",
        "2captcha": "TWOCAPTCHA_API_KEY",
        "anticaptcha": "ANTICAPTCHA_API_KEY",
        "nopecha": "NOPECHA_API_KEY",
    }
    return CaptchaSolverBypass(
        provider=provider,
        api_key=os.environ.get(env_keys.get(provider, ""), ""),
        wait_seconds=float(config["wait_seconds"]) if "wait_seconds" in config else None,
        max_attempts=int(config.get("max_attempts", "2")),
        max_paid_attempts=int(config.get("max_paid_attempts", "1")),
        proxy_url=str(config.get("proxy_url", "")),
        enabled_providers=frozenset(settings.captcha_enabled_providers),
    )


register_bypass(
    "captcha_solver",
    capability=BypassCapability(
        cost=50,
        browser_family="decorator",
        challenge_actions=frozenset({"captcha_solver"}),
    ),
)(_create_captcha_solver)
