"""CAPTCHA challenge solver — composable decorator for BypassStrategy.

Supports three solving modes:
1. Browser-wait: open the challenge page in a real browser, wait for the
   anti-bot JS to resolve (Cloudflare Turnstile auto-solves with a good
   fingerprint), then harvest clearance cookies. Free, no API key needed.
2. Token extraction: for hCaptcha/reCAPTCHA embedded in career sites,
   wait for the challenge widget to appear and extract the response token
   via JS evaluation. Free.
3. External API: delegate to NopeCHA (free/dev), CapSolver, CapMonster,
   NextCaptcha, 2captcha, or anticaptcha when browser-wait fails. Requires an
   API key via the provider's env var (see `_create_captcha_solver`).

The solver is a composable decorator, not a standalone escalation tier.
It wraps apply_page() to add challenge detection and cookie harvesting
on top of whatever browser tier is active.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import structlog

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.infrastructure.bypass.captcha_models import (
    CaptchaChallengeType,
    CaptchaFailureReason,
    CaptchaResultKind,
    CaptchaSolveResult,
)
from job_ftch.infrastructure.bypass.captcha_providers import (
    extract_sitekey,
    normalize_challenge_type,
    resolve_captcha_provider,
)
from job_ftch.infrastructure.sources.source_deadline import (
    remaining_source_seconds,
    sleep_with_source_deadline,
)

logger = structlog.get_logger("job_ftch.bypass.captcha")

CAPTCHA_PROVIDER_ENV_KEYS = {
    "capsolver": "CAPSOLVER_API_KEY",
    "capmonster": "CAPMONSTER_API_KEY",
    "nextcaptcha": "NEXTCAPTCHA_API_KEY",
    "2captcha": "TWOCAPTCHA_API_KEY",
    "anticaptcha": "ANTICAPTCHA_API_KEY",
    "nopecha": "NOPECHA_API_KEY",
    "cliproxy_image": "JOB_FTCH_OPENAI_API_KEY",
}


def _provider_api_key(provider_name: str) -> str:
    """Return the env/settings key for one captcha provider.

    Image OCR reuses the OpenAI-compatible client key (CLIProxy or OpenAI).
    There is no separate ``JOB_FTCH_CAPTCHA_VISION_API_KEY``.
    """
    if provider_name == "cliproxy_image":
        from job_ftch.config import get_settings

        secret = get_settings().openai_api_key
        if secret is not None:
            value = str(secret.get_secret_value() or "").strip()
            if value:
                return value
        return (
            os.environ.get("JOB_FTCH_OPENAI_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
    env_name = CAPTCHA_PROVIDER_ENV_KEYS.get(provider_name, "")
    return os.environ.get(env_name, "").strip() if env_name else ""


def _counts_as_paid_captcha_provider(name: str) -> bool:
    # Subscription vision OCR must still run after the one paid CapSolver slot.
    return name != "cliproxy_image"


def _consumes_paid_captcha_slot(result: CaptchaSolveResult) -> bool:
    """False when the provider never attempted a billable solve."""
    if result.failure_reason in {
        CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
        CaptchaFailureReason.MISSING_CREDENTIAL,
        CaptchaFailureReason.PROVIDER_DISABLED,
        CaptchaFailureReason.UNAUTHORIZED_DOMAIN,
    }:
        return False
    blob = f"{result.raw_provider_status or ''} {result.error or ''}"
    return "TYPE_NOT_SUPPORTED" not in blob and "TASK_NOT_SUPPORTED" not in blob


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
        backoff_seconds: float = 300.0,
        proxy_url: str = "",
        enabled_providers: frozenset[str] | None = None,
        provider_routes: dict[str, tuple[str, ...]] | None = None,
        authorized_domains: frozenset[str] | None = None,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        # Provider-backed (paid/external) solving is restricted to owned or
        # explicitly authorized targets. The free passive ``browser_wait`` tier
        # is always allowed and never gated here.
        #
        # ``None`` means the gate is unconfigured (inactive) — used by unit tests
        # that exercise provider mechanics directly. A frozenset means the gate
        # is active; an EMPTY frozenset therefore authorizes nothing, which is
        # the deny-by-default the production factory passes.
        self._authorized_domains: frozenset[str] | None = (
            None
            if authorized_domains is None
            else frozenset(d.strip().lower().lstrip(".") for d in authorized_domains if d.strip())
        )
        # browser_wait is always permitted (free, no external call); the external
        # provider only fires when it appears in the allowlist.
        self._enabled_providers = (
            frozenset(enabled_providers) if enabled_providers is not None else None
        )
        self._wait = wait_seconds or self.DEFAULT_WAIT_SECONDS
        self._cookie_cache: dict[str, _CookieCache] = {}
        self._max_attempts = max(1, max_attempts)
        self._max_paid_attempts = max(0, max_paid_attempts)
        # ponytail: per-detail budget keeps one blocked card from poisoning
        # sibling cards; a source-wide quota remains runtime policy.
        self._attempts: dict[tuple[str, str, str], int] = {}
        self._paid_attempts: dict[tuple[str, str, str], int] = {}
        self._min_provider_seconds = max(0.0, min_provider_seconds)
        self._paid_lock = asyncio.Lock()
        self._proxy_url = proxy_url
        self._provider_routes = {
            "image": ("capsolver", "cliproxy_image", "observe"),
            **(provider_routes or {}),
        }
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._failure_backoff: dict[tuple[str, str, str], float] = {}
        self._last_result: CaptchaSolveResult | None = None

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

            domain = (urlparse(url).hostname or urlparse(url).netloc).lower()
        elif hasattr(page, "url"):
            from urllib.parse import urlparse

            parsed_page_url = urlparse(str(page.url))
            domain = (parsed_page_url.hostname or parsed_page_url.netloc).lower()
        normalized_type = normalize_challenge_type(challenge_type)
        manual_mode = self._provider in {"manual_required", "observe"}
        request_url = url or str(getattr(page, "url", ""))
        attempt_key = self._attempt_key(domain, normalized_type, request_url)
        backoff_key = attempt_key
        backoff_until = self._failure_backoff.get(backoff_key)
        if not manual_mode and domain and backoff_until and backoff_until > time.monotonic():
            return CaptchaSolveResult(
                solved=False,
                method="backoff",
                error="recent solver failure backoff is active",
                failure_reason=CaptchaFailureReason.BACKOFF_ACTIVE,
                challenge_type=normalized_type,
                raw_provider_status=f"retry_after_seconds={backoff_until - time.monotonic():.1f}",
            )

        cached = self._cookie_cache.get(domain) if not manual_mode else None
        if cached and not cached.expired:
            return CaptchaSolveResult(
                solved=True,
                method="cache",
                cookies=cached.cookies,
                elapsed_seconds=0.0,
                challenge_type=normalized_type,
                result_kind=CaptchaResultKind.SESSION,
            )

        if not manual_mode and self._attempts.get(attempt_key, 0) >= self._max_attempts:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="solver attempt budget exhausted",
                failure_reason=CaptchaFailureReason.BUDGET_EXHAUSTED,
                challenge_type=normalized_type,
            )
        if not manual_mode:
            self._attempts[attempt_key] = self._attempts.get(attempt_key, 0) + 1

        provider_authorized = self._domain_authorized(domain)
        provider_chain = () if manual_mode else self._provider_chain_for(challenge_type)
        if provider_chain and not provider_authorized:
            # Off-allowlist domains keep only the free passive wait; paid/
            # external providers are dropped from the chain.
            filtered = tuple(
                name
                for name in provider_chain
                if name in {"browser_wait", "observe", "manual_required"}
            )
            if filtered != provider_chain:
                logger.info(
                    "captcha_provider_blocked_unauthorized",
                    domain=domain,
                    dropped=[name for name in provider_chain if name != "browser_wait"],
                )
            provider_chain = filtered
        chain_already_verified = False
        if provider_chain:
            result = await self._solve_provider_chain(
                page, challenge_type, url, provider_chain, attempt_key
            )
            chain_already_verified = True
        elif self._provider == "manual_required":
            result = await self._wait_for_manual_clearance(page, challenge_type, url)
        elif self._provider == "observe":
            result = CaptchaSolveResult(
                solved=False,
                method="observe",
                challenge_type=normalized_type,
                result_kind=CaptchaResultKind.SKIPPED_OBSERVE,
            )
        elif self._provider == "browser_wait":
            result = await self._solve_browser_wait(page, challenge_type)
        elif not provider_authorized:
            logger.info(
                "captcha_provider_blocked_unauthorized",
                domain=domain,
                provider=self._provider,
            )
            result = CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="provider CAPTCHA solving is not authorized for this domain",
                failure_reason=CaptchaFailureReason.UNAUTHORIZED_DOMAIN,
                challenge_type=normalized_type,
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        elif self._enabled_providers is not None and self._provider not in self._enabled_providers:
            result = CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error=f"provider '{self._provider}' disabled in settings",
                failure_reason=CaptchaFailureReason.PROVIDER_DISABLED,
                challenge_type=normalized_type,
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        else:
            remaining = remaining_source_seconds()
            if remaining is not None and remaining < self._min_provider_seconds:
                result = CaptchaSolveResult(
                    solved=False,
                    method=self._provider,
                    error="source deadline cannot cover provider timeout",
                    failure_reason=CaptchaFailureReason.DEADLINE_INSUFFICIENT,
                    challenge_type=normalized_type,
                )
            else:
                async with self._paid_lock:
                    if self._paid_attempts.get(attempt_key, 0) >= self._max_paid_attempts:
                        result = CaptchaSolveResult(
                            solved=False,
                            method=self._provider,
                            error="paid solver budget exhausted",
                            failure_reason=CaptchaFailureReason.BUDGET_EXHAUSTED,
                            challenge_type=normalized_type,
                        )
                    else:
                        self._paid_attempts[attempt_key] = (
                            self._paid_attempts.get(attempt_key, 0) + 1
                        )
                        result = await self._solve_external_api(page, challenge_type, url)
        if not chain_already_verified:
            result = await self._apply_solved_result(page, result, challenge_type, domain)

        result.elapsed_seconds = time.monotonic() - start
        if result.challenge_type == CaptchaChallengeType.UNKNOWN.value:
            result.challenge_type = normalized_type
        logger.info(
            "captcha_attempt_result",
            provider=result.method,
            challenge_type=result.challenge_type,
            solved=result.solved,
            failure_reason=result.failure_reason,
            result_kind=result.result_kind,
            provider_task_id=bool(result.provider_task_id),
            cost_estimate_usd=result.cost_estimate_usd,
            token_present=bool(result.tokens),
            cookie_count=len(result.cookies),
        )

        if result.solved and domain and result.cookies:
            self._cookie_cache[domain] = _CookieCache(
                cookies=result.cookies,
                harvested_at=time.monotonic(),
            )
            self._failure_backoff.pop(backoff_key, None)
        elif (
            domain
            and self._backoff_seconds > 0
            and result.failure_reason
            in {
                CaptchaFailureReason.PROVIDER_REJECTED,
                CaptchaFailureReason.PROVIDER_TIMEOUT,
                CaptchaFailureReason.BAD_TOKEN,
                CaptchaFailureReason.INJECTION_FAILED,
                CaptchaFailureReason.VERIFICATION_FAILED,
            }
        ):
            self._failure_backoff[backoff_key] = time.monotonic() + self._backoff_seconds

        self._last_result = result
        return result

    def _provider_chain_for(self, challenge_type: str) -> tuple[str, ...]:
        normalized = normalize_challenge_type(challenge_type)
        providers = self._provider_routes.get(normalized) or self._provider_routes.get("unknown")
        return tuple(providers or ())

    @staticmethod
    def _attempt_key(
        domain: str,
        challenge_type: str,
        url: str,
    ) -> tuple[str, str, str]:
        from urllib.parse import urlparse

        return domain, challenge_type, urlparse(url).path or "/"

    async def _apply_solved_result(
        self,
        page: Any,
        result: CaptchaSolveResult,
        challenge_type: str,
        domain: str,
    ) -> CaptchaSolveResult:
        """Inject a provider token/session and confirm the challenge actually cleared."""
        normalized_type = normalize_challenge_type(challenge_type)
        if result.solved and result.tokens:
            token = result.tokens.get("captcha_token", "")
            if token and not await self._inject_token(page, challenge_type, token):
                return CaptchaSolveResult(
                    solved=False,
                    method=result.method,
                    error="provider token could not be injected",
                    failure_reason=CaptchaFailureReason.INJECTION_FAILED,
                    challenge_type=normalized_type,
                    result_kind=CaptchaResultKind.TOKEN,
                    provider_task_id=result.provider_task_id,
                    raw_provider_status=result.raw_provider_status,
                )
            if token and not await self._check_challenge_cleared(page, challenge_type):
                return CaptchaSolveResult(
                    solved=False,
                    method=result.method,
                    error="challenge remained after token injection",
                    failure_reason=CaptchaFailureReason.VERIFICATION_FAILED,
                    challenge_type=normalized_type,
                    result_kind=CaptchaResultKind.TOKEN,
                    provider_task_id=result.provider_task_id,
                    raw_provider_status=result.raw_provider_status,
                )
        elif result.solved and result.cookies:
            if not await self._apply_clearance_cookies(page, domain, result.cookies):
                return CaptchaSolveResult(
                    solved=False,
                    method=result.method,
                    error="provider cookies could not be applied",
                    failure_reason=CaptchaFailureReason.INJECTION_FAILED,
                    challenge_type=normalized_type,
                    result_kind=CaptchaResultKind.SESSION,
                    provider_task_id=result.provider_task_id,
                    raw_provider_status=result.raw_provider_status,
                )
        return result

    async def _solve_provider_chain(
        self,
        page: Any,
        challenge_type: str,
        url: str,
        provider_chain: tuple[str, ...],
        attempt_key: tuple[str, str, str],
    ) -> CaptchaSolveResult:
        from urllib.parse import urlparse

        failures: list[str] = []
        domain = (urlparse(url).hostname or urlparse(url).netloc).lower() if url else ""
        for provider_name in provider_chain:
            if provider_name == "manual_required":
                return await self._wait_for_manual_clearance(page, challenge_type, url)
            if provider_name == "observe":
                if failures:
                    return CaptchaSolveResult(
                        solved=False,
                        method="provider_chain",
                        error="all captcha providers failed",
                        failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
                        challenge_type=normalize_challenge_type(challenge_type),
                        raw_provider_status=";".join(failures),
                    )
                return CaptchaSolveResult(
                    solved=False,
                    method=provider_name,
                    error=f"provider chain stopped at {provider_name}",
                    failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                    challenge_type=normalize_challenge_type(challenge_type),
                    result_kind=CaptchaResultKind.SKIPPED_OBSERVE,
                    raw_provider_status=";".join(failures),
                )
            previous_provider = self._provider
            previous_api_key = self._api_key
            self._provider = provider_name
            self._api_key = _provider_api_key(provider_name)
            try:
                if provider_name == "browser_wait":
                    result = await self._solve_browser_wait(page, challenge_type)
                elif (
                    self._enabled_providers is not None
                    and provider_name not in self._enabled_providers
                ):
                    result = CaptchaSolveResult(
                        solved=False,
                        method=provider_name,
                        error=f"provider '{provider_name}' disabled in settings",
                        failure_reason=CaptchaFailureReason.PROVIDER_DISABLED,
                        challenge_type=normalize_challenge_type(challenge_type),
                        result_kind=CaptchaResultKind.UNSUPPORTED,
                    )
                else:
                    remaining = remaining_source_seconds()
                    if remaining is not None and remaining < self._min_provider_seconds:
                        result = CaptchaSolveResult(
                            solved=False,
                            method=provider_name,
                            error="source deadline cannot cover provider timeout",
                            failure_reason=CaptchaFailureReason.DEADLINE_INSUFFICIENT,
                            challenge_type=normalize_challenge_type(challenge_type),
                        )
                    elif not self._api_key or not _counts_as_paid_captcha_provider(provider_name):
                        # Missing credentials and cliproxy_image (subscription OCR)
                        # do not consume the paid CapSolver slot.
                        result = await self._solve_external_api(
                            page,
                            challenge_type,
                            url,
                        )
                    else:
                        async with self._paid_lock:
                            if self._paid_attempts.get(attempt_key, 0) >= self._max_paid_attempts:
                                result = CaptchaSolveResult(
                                    solved=False,
                                    method=provider_name,
                                    error="paid solver budget exhausted",
                                    failure_reason=CaptchaFailureReason.BUDGET_EXHAUSTED,
                                    challenge_type=normalize_challenge_type(challenge_type),
                                )
                            else:
                                result = await self._solve_external_api(
                                    page,
                                    challenge_type,
                                    url,
                                )
                                if _consumes_paid_captcha_slot(result):
                                    self._paid_attempts[attempt_key] = (
                                        self._paid_attempts.get(attempt_key, 0) + 1
                                    )
            finally:
                self._provider = previous_provider
                self._api_key = previous_api_key
            if result.solved:
                result = await self._apply_solved_result(page, result, challenge_type, domain)
                if result.solved:
                    return result
            provider_status = result.raw_provider_status or str(result.failure_reason or "failed")
            failures.append(f"{provider_name}:{provider_status}")
            if result.failure_reason is CaptchaFailureReason.DEADLINE_INSUFFICIENT:
                return result
            # Paid budget exhaustion must not skip free OCR (cliproxy_image).
        return CaptchaSolveResult(
            solved=False,
            method="provider_chain",
            error="all captcha providers failed",
            failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
            challenge_type=normalize_challenge_type(challenge_type),
            raw_provider_status=";".join(failures),
        )

    async def solve_detected(self, page: Any, *, url: str = "") -> CaptchaSolveResult:
        """Detect and solve on the current page without changing its session."""
        challenge_type = await self._detect_challenge(page)
        if not challenge_type:
            return CaptchaSolveResult(
                solved=False,
                method="none",
                error="no supported challenge detected",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        return await self.solve(page, challenge_type=challenge_type, url=url)

    async def _wait_for_manual_clearance(
        self, page: Any, challenge_type: str, expected_url: str
    ) -> CaptchaSolveResult:
        """Only observe; the operator submits the form in the same open session."""
        from urllib.parse import urlsplit

        from job_ftch.infrastructure.bypass.challenge_classifier import (
            classify_challenge,
            emit_challenge_detection,
        )
        from job_ftch.infrastructure.bypass.failure_signal import FailureKind

        initial_url = str(getattr(page, "url", ""))
        initial = urlsplit(initial_url)
        expected = urlsplit(expected_url)
        started = time.monotonic()
        first = True
        while True:
            html = await page.content()
            current = urlsplit(str(getattr(page, "url", "")))
            detection = classify_challenge(
                surface="manual_captcha_wait",
                status_code=200,
                body=html,
                page_url=str(getattr(page, "url", "") or ""),
            )
            if first:
                emit_challenge_detection(initial.hostname or "", detection)
                logger.info("captcha_manual_required", type=challenge_type)
                first = False
            target_matches = bool(expected.hostname) and (
                current.scheme,
                current.netloc,
                current.path,
                current.query,
            ) == (expected.scheme, expected.netloc, expected.path, expected.query)
            navigated = (current.netloc, current.path) != (initial.netloc, initial.path)
            if (
                detection.kind == FailureKind.OK
                and not detection.detected
                and current.hostname == initial.hostname
                and (target_matches if expected_url else navigated)
                and await page.evaluate("document.readyState") == "complete"
                and await page.evaluate(
                    "performance.getEntriesByType('navigation').at(-1)?.responseStatus === 200"
                )
                and await page.evaluate("Boolean(document.body && document.body.innerText.trim())")
            ):
                return CaptchaSolveResult(
                    solved=True,
                    method="manual_required",
                    challenge_type=challenge_type,
                    result_kind=CaptchaResultKind.SESSION,
                )
            if time.monotonic() - started >= self._wait:
                return CaptchaSolveResult(
                    solved=False,
                    method="manual_required",
                    challenge_type=challenge_type,
                    result_kind=CaptchaResultKind.MANUAL_REQUIRED,
                    error="manual completion not confirmed; retry in the operator session",
                )
            await sleep_with_source_deadline(
                max(0.0, min(self.POLL_INTERVAL_SECONDS, self._wait - (time.monotonic() - started)))
            )

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
            from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge

            detection = classify_challenge(
                surface="browser_dom",
                status_code=200,
                body=html,
                page_url=str(getattr(page, "url", "") or ""),
            )
            if detection.detected and detection.challenge_type:
                normalized = normalize_challenge_type(detection.challenge_type)
                if normalized != CaptchaChallengeType.UNKNOWN.value:
                    return normalized
            from job_ftch.infrastructure.bypass.failure_signal import _detect_captcha_type

            if _detect_captcha_type(html) == "recaptcha_v3":
                return CaptchaChallengeType.RECAPTCHA_V3.value

            if any(
                marker in html_lower
                for marker in (
                    "smartcaptcha",
                    "showcaptcha",
                    "tmgrdfrend",
                    "cian-captcha",
                    "smart-token",
                    "ysc1_",
                    "smartcaptcha.cloud.yandex.ru",
                )
            ):
                return CaptchaChallengeType.SMARTCAPTCHA.value

            if any(
                marker in html_lower
                for marker in (
                    "cf-turnstile",
                    "turnstile-wrapper",
                    'data-sitekey="0x',
                    "data-sitekey='0x",
                    "challenges.cloudflare.com/turnstile",
                )
            ):
                return "turnstile"

            if any(
                marker in html_lower
                for marker in (
                    "challenge-running",
                    "challenge-stage",
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
            from selectolax.parser import HTMLParser

            if HTMLParser(html).css_first(
                'img[class*="captcha-image"], img[class*="captcha_image"], img[src*="captcha"]'
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
        if normalize_challenge_type(challenge_type) == CaptchaChallengeType.SMARTCAPTCHA.value:
            await self._click_smartcaptcha_checkbox(page)
            await sleep_with_source_deadline(min(1.5, max(self._wait, 0.05)))
            await self._solve_smartcaptcha_image_clicks(page)
        elapsed = 0.0
        poll = self.POLL_INTERVAL_SECONDS

        while elapsed < self._wait:
            await sleep_with_source_deadline(poll)
            elapsed += poll

            cleared = await self._check_challenge_cleared(
                page, challenge_type, wait_for_redirect=False
            )
            if cleared:
                cookies = await self._harvest_cookies(page)
                method = "browser_wait"
                normalized_type = normalize_challenge_type(challenge_type)
                if normalized_type in {
                    CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
                    CaptchaChallengeType.TURNSTILE.value,
                }:
                    method = await self._detect_turnstile_method(page)
                return CaptchaSolveResult(
                    solved=True,
                    method=method,
                    cookies=cookies,
                    challenge_type=normalize_challenge_type(challenge_type),
                    result_kind=CaptchaResultKind.SESSION,
                )

        extended = await self._try_extended_wait(page, challenge_type)
        if extended:
            cookies = await self._harvest_cookies(page)
            return CaptchaSolveResult(
                solved=True,
                method="browser_wait_extended",
                cookies=cookies,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.SESSION,
            )

        return CaptchaSolveResult(
            solved=False,
            method="browser_wait",
            error=f"challenge not cleared after {self._wait}s",
            failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
            challenge_type=normalize_challenge_type(challenge_type),
            result_kind=CaptchaResultKind.SESSION,
        )

    async def _click_smartcaptcha_checkbox(self, page: Any) -> bool:
        """Click the Yandex checkbox, including inside a cross-origin iframe."""
        host_selectors = (
            ".CheckboxCaptcha-Button",
            ".CheckboxCaptcha",
            '[role="checkbox"]',
            ".smart-captcha",
            "#smartcaptcha-container",
            'iframe[src*="smartcaptcha"]',
        )
        click = getattr(page, "click", None)
        if callable(click):
            for selector in host_selectors:
                try:
                    await click(selector, timeout=2000)
                    return True
                except Exception:
                    continue
        frame_locator = getattr(page, "frame_locator", None)
        if callable(frame_locator):
            try:
                inner = frame_locator('iframe[src*="smartcaptcha"]')
                get_by_role = getattr(inner, "get_by_role", None)
                if callable(get_by_role):
                    await get_by_role("checkbox").click(timeout=2000)
                    return True
            except Exception:
                pass
            try:
                inner = frame_locator('iframe[src*="smartcaptcha"]')
                locator = getattr(inner, "locator", None)
                if callable(locator):
                    await locator(".CheckboxCaptcha-Button").click(timeout=2000)
                    return True
            except Exception:
                pass
        frames = getattr(page, "frames", None)
        frame_list = frames if isinstance(frames, (list, tuple)) else ()
        if callable(frames):
            try:
                frame_list = frames()
            except Exception:
                frame_list = ()
        for frame in frame_list or ():
            frame_click = getattr(frame, "click", None)
            if not callable(frame_click):
                continue
            for selector in (
                ".CheckboxCaptcha-Button",
                ".CheckboxCaptcha",
                '[role="checkbox"]',
                "text=I'm not a robot",
                "text=Я не робот",
                "text=Press to continue",
            ):
                try:
                    await frame_click(selector, timeout=2000)
                    return True
                except Exception:
                    continue
        return False

    async def _smartcaptcha_puzzle_visible(self, page: Any) -> bool:
        markers = (
            "press in the following order",
            "нажмите в следующем порядке",
            "additional check",
        )
        texts: list[str] = []
        evaluate = getattr(page, "evaluate", None)
        if callable(evaluate):
            with suppress(Exception):
                texts.append(str(await evaluate("document.body ? document.body.innerText : ''")))
        frames = getattr(page, "frames", None)
        frame_list = frames if isinstance(frames, (list, tuple)) else ()
        for frame in frame_list or ():
            frame_eval = getattr(frame, "evaluate", None)
            if not callable(frame_eval):
                continue
            try:
                texts.append(str(await frame_eval("document.body ? document.body.innerText : ''")))
            except Exception:
                continue
        blob = " ".join(texts).lower()
        return any(marker in blob for marker in markers)

    async def _solve_smartcaptcha_image_clicks(self, page: Any) -> bool:
        """Click the SmartCaptcha image puzzle using the configured vision model."""
        screenshot = getattr(page, "screenshot", None)
        mouse = getattr(page, "mouse", None)
        if not callable(screenshot) or mouse is None or not callable(getattr(mouse, "click", None)):
            return False
        try:
            png = await screenshot(full_page=False)
        except TypeError:
            try:
                png = await screenshot()
            except Exception:
                return False
        except Exception:
            return False
        if not isinstance(png, (bytes, bytearray)) or not png:
            return False
        from job_ftch.infrastructure.bypass.captcha_providers import request_vision_click_order

        points = await request_vision_click_order(bytes(png))
        logger.info("smartcaptcha_vision_clicks", points=len(points), png_bytes=len(png))
        if not points:
            return False
        viewport = getattr(page, "viewport_size", None) or {}
        width = int(viewport.get("width") or 0)
        height = int(viewport.get("height") or 0)
        if width <= 0 or height <= 0:
            try:
                size = await page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
                width = int(size.get("w") or 0)
                height = int(size.get("h") or 0)
            except Exception:
                return False
        if width <= 0 or height <= 0:
            return False
        for x_frac, y_frac in points:
            try:
                await mouse.click(x_frac * width, y_frac * height)
            except Exception:
                return False
            await sleep_with_source_deadline(0.35)
        click = getattr(page, "click", None)
        if callable(click):
            for selector in (
                "text=Submit",
                "text=Отправить",
                'button:has-text("Submit")',
            ):
                try:
                    await click(selector, timeout=2000)
                    break
                except Exception:
                    continue
        frames = getattr(page, "frames", None)
        frame_list = frames if isinstance(frames, (list, tuple)) else ()
        for frame in frame_list or ():
            frame_click = getattr(frame, "click", None)
            if not callable(frame_click):
                continue
            for selector in ("text=Submit", "text=Отправить", "button"):
                try:
                    await frame_click(selector, timeout=2000)
                    return True
                except Exception:
                    continue
        return True

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
            normalized_type = normalize_challenge_type(challenge_type)
            if normalized_type in {
                CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
                CaptchaChallengeType.TURNSTILE.value,
            }:
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

            elif (
                normalize_challenge_type(challenge_type) == CaptchaChallengeType.SMARTCAPTCHA.value
            ):
                await self._click_smartcaptcha_checkbox(page)

            extra_wait = min(self._wait, self.MAX_WAIT_SECONDS - self._wait)
            if extra_wait > 0:
                await sleep_with_source_deadline(extra_wait)
                return await self._check_challenge_cleared(page, challenge_type)
        except Exception:
            pass
        return False

    async def _check_challenge_cleared(
        self,
        page: Any,
        challenge_type: str,
        *,
        wait_for_redirect: bool = True,
    ) -> bool:
        try:
            normalized_type = normalize_challenge_type(challenge_type)
            if normalized_type == CaptchaChallengeType.IMAGE.value:
                from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge

                for _ in range(10):
                    try:
                        html = await page.content()
                        detection = classify_challenge(
                            surface="image_captcha_clear_check", status_code=200, body=html
                        )
                        ready = await page.evaluate("document.readyState")
                        active_image_form = await page.evaluate(
                            "Boolean([...document.querySelectorAll('form')].some(form => "
                            "form.querySelector('input[name=\"captchaText\"]') && "
                            "form.querySelector('img') && form.getClientRects().length))"
                        )
                        successful_document = await page.evaluate(
                            "(() => { const status = performance.getEntriesByType('navigation')"
                            ".at(-1)?.responseStatus; return !status || (status >= 200 && status < 400); })()"
                        )
                        if (
                            not detection.detected
                            and not active_image_form
                            and ready in ("interactive", "complete")
                            and successful_document
                            and await page.evaluate(
                                "Boolean(document.body && document.body.innerText.trim().length > 100)"
                            )
                        ):
                            return True
                    except Exception:
                        pass  # Form submission can briefly destroy the execution context.
                    await sleep_with_source_deadline(1.0)
                return False
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

                if normalized_type in {
                    CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
                    CaptchaChallengeType.TURNSTILE.value,
                }:
                    if any(
                        marker in body_lower
                        for marker in (
                            "checking your browser",
                            "just a moment",
                            "performing security verification",
                            "protect against malicious bots",
                            "protects against malicious bots",
                            "performance and security by cloudflare",
                        )
                    ):
                        return False
                    html = str(
                        await page.evaluate(
                            "document.documentElement ? "
                            "document.documentElement.outerHTML.substring(0, 50000) : ''"
                        )
                    )
                    if html:
                        from job_ftch.infrastructure.bypass.challenge_classifier import (
                            classify_challenge,
                        )

                        detection = classify_challenge(
                            surface="browser_clear_check",
                            status_code=200,
                            body=html,
                            page_url=str(getattr(page, "url", "") or ""),
                        )
                        if detection.detected:
                            return False
                    if normalized_type == CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value:
                        return bool(await self._harvest_cookies(page))
                    turnstile_token = await page.evaluate(
                        "(()=>{"
                        "const el=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                        "return el?el.value:'';})()"
                    )
                    if turnstile_token:
                        return True
                    if len(body_text) > 100:
                        return True

                elif normalized_type == CaptchaChallengeType.SMARTCAPTCHA.value:
                    from job_ftch.infrastructure.bypass.challenge_classifier import (
                        classify_challenge,
                    )
                    from job_ftch.infrastructure.bypass.failure_signal import is_smartcaptcha_url

                    poll_seconds = min(1.0, max(float(self._wait or 0.01), 0.01))
                    attempts = 10 if wait_for_redirect else 1
                    for _ in range(attempts):
                        page_url = str(getattr(page, "url", "") or "")
                        html = str(
                            await page.evaluate(
                                "document.documentElement ? "
                                "document.documentElement.outerHTML.substring(0, 50000) : ''"
                            )
                        )
                        still_on_interstitial = bool(page_url) and is_smartcaptcha_url(page_url)
                        widget_still_up = False
                        if html:
                            widget_still_up = classify_challenge(
                                surface="smartcaptcha_clear_check",
                                status_code=200,
                                body=html,
                                page_url=page_url,
                            ).detected
                        if not still_on_interstitial and not widget_still_up:
                            return True
                        await sleep_with_source_deadline(poll_seconds)
                    return False

                elif normalized_type in (
                    CaptchaChallengeType.HCAPTCHA.value,
                    CaptchaChallengeType.RECAPTCHA.value,
                ):
                    token = await page.evaluate(
                        "(()=>{"
                        "const el=document.querySelector('[name=\"h-captcha-response\"]')"
                        "||document.querySelector('[name=\"g-recaptcha-response\"]');"
                        "return el?el.value:'';})()"
                    )
                    return bool(token)

                elif normalized_type == CaptchaChallengeType.RECAPTCHA_V3.value:
                    token = await page.evaluate(
                        "(()=>{"
                        "const el=document.querySelector('[name=\"g-recaptcha-response\"]');"
                        "return el?el.value:'';})()"
                    )
                    if not token:
                        return False
                    body_text_full = str(
                        await page.evaluate(
                            "document.body ? document.body.innerText.substring(0, 2000) : ''"
                        )
                    )
                    body_lower_full = body_text_full.lower()
                    blocked_markers = (
                        "captcha",
                        "verify you are human",
                        "checking your browser",
                        "access denied",
                        "403",
                    )
                    return len(body_text_full.strip()) > 100 and not any(
                        marker in body_lower_full for marker in blocked_markers
                    )

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

    async def _apply_clearance_cookies(
        self,
        page: Any,
        domain: str,
        cookies: dict[str, str],
    ) -> bool:
        if not domain or not cookies:
            return False
        cookie_items = [
            {
                "name": str(name),
                "value": str(value),
                "domain": domain,
                "path": "/",
                "secure": True,
            }
            for name, value in cookies.items()
            if str(name).strip() and str(value).strip()
        ]
        if not cookie_items:
            return False
        try:
            add_cookies = getattr(page, "add_cookies", None)
            if callable(add_cookies):
                await add_cookies(cookie_items)
                return True
            context = getattr(page, "context", None)
            context_add = getattr(context, "add_cookies", None)
            if callable(context_add):
                await context_add(cookie_items)
                return True
            if hasattr(page, "evaluate"):
                applied = False
                for cookie in cookie_items:
                    applied = (
                        bool(
                            await page.evaluate(
                                """(cookie) => {
                              document.cookie = `${cookie.name}=${cookie.value}; path=/; SameSite=None; Secure`;
                              return document.cookie.includes(`${cookie.name}=`);
                            }""",
                                cookie,
                            )
                        )
                        or applied
                    )
                return applied
        except Exception as exc:
            logger.debug("captcha_cookie_apply_error", error=str(exc))
        return False

    def set_proxy_url(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url

    def _domain_authorized(self, domain: str) -> bool:
        """Whether provider-backed solving is authorized for ``domain``.

        Matches the domain or any parent suffix against the allowlist, so
        ``jobs.example.com`` is covered by an ``example.com`` entry. ``*``
        authorizes every domain. An empty allowlist authorizes nothing
        (safe default); ``browser_wait`` is not gated by this and stays
        available everywhere.
        """
        if self._authorized_domains is None:
            return True  # gate not configured -> unrestricted
        if not self._authorized_domains:
            return False  # configured but empty -> deny by default
        if "*" in self._authorized_domains:
            return True
        host = domain.strip().lower().lstrip(".")
        if not host:
            return False
        parts = host.split(".")
        candidates = {".".join(parts[i:]) for i in range(len(parts))}
        return bool(candidates & self._authorized_domains)

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
                challenge_type=normalize_challenge_type(challenge_type),
            )
        if not self._api_key:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="no api_key configured",
                failure_reason=CaptchaFailureReason.MISSING_CREDENTIAL,
                challenge_type=normalize_challenge_type(challenge_type),
            )

        try:
            result = await provider.solve(page, challenge_type=challenge_type, url=url)
        except TimeoutError:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="provider timed out",
                failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
                challenge_type=normalize_challenge_type(challenge_type),
            )
        except Exception as exc:
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error=type(exc).__name__,
                failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
                challenge_type=normalize_challenge_type(challenge_type),
            )
        if not isinstance(result, CaptchaSolveResult):
            return CaptchaSolveResult(
                solved=False,
                method=self._provider,
                error="provider returned a malformed result",
                failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
                challenge_type=normalize_challenge_type(challenge_type),
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
            if (
                normalize_challenge_type(challenge_type) == CaptchaChallengeType.IMAGE.value
                and callable(getattr(page, "fill", None))
                and callable(getattr(page, "click", None))
                and await page.evaluate(
                    "Boolean(document.querySelector('input[name=\"captchaText\"]'))"
                )
            ):
                await page.fill('input[name="captchaText"]:visible', token, timeout=10_000)
                await page.click(
                    'form:has(input[name="captchaText"]:visible) button[type="submit"]',
                    timeout=10_000,
                )
                return True
            return bool(
                await page.evaluate(
                    """({token, challengeType}) => {
                      const normalized = String(challengeType || '').toLowerCase();
                      if (normalized === 'image') {
                        const img = document.querySelector(
                          'img[data-qa*="captcha"], img[alt="captcha" i], img[class*="captcha-image"], img[class*="captcha_image"], img[src*="captcha"]'
                        );
                        const form = img && (img.closest('form') ||
                          [...document.forms].find(candidate => candidate.querySelector('input[name="captchaText"]')));
                        if (!form) return false;
                        const inputs = [...form.querySelectorAll('input')].filter(el =>
                          !el.disabled && ['text', 'search', 'tel'].includes(el.type) &&
                          /captcha/i.test(`${el.name} ${el.id} ${el.getAttribute('data-qa') || ''}`)
                        );
                        if (inputs.length !== 1) return false;
                        const input = inputs[0];
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(input, token);
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        if (!form.checkValidity()) return false;
                        const submitter = form.querySelector('button[type="submit"], input[type="submit"]');
                        if (submitter && submitter.disabled) return false;
                        if (submitter) submitter.click();
                        else form.requestSubmit();
                        return true;
                      }
                      if (normalized === 'smartcaptcha') {
                        const root = document.querySelector(
                          '.smart-captcha, #smartcaptcha-container, [data-sitekey^="ysc1_"]'
                        ) || document.forms[0] || document.body || document.documentElement;
                        let input = document.querySelector(
                          'input[name="smart-token"], textarea[name="smart-token"]'
                        );
                        if (!input) {
                          input = document.createElement('input');
                          input.type = 'hidden';
                          input.name = 'smart-token';
                          root.appendChild(input);
                        }
                        const proto = input.tagName === 'TEXTAREA'
                          ? HTMLTextAreaElement.prototype
                          : HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')
                          && Object.getOwnPropertyDescriptor(proto, 'value').set;
                        if (setter) setter.call(input, token);
                        else input.value = token;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        const invoke = (fn) => {
                          if (typeof fn !== 'function') return false;
                          try { fn(token); return true; } catch (_) { return false; }
                        };
                        for (const el of document.querySelectorAll('[data-callback]')) {
                          const name = el.getAttribute('data-callback');
                          const fn = name && name.split('.').reduce(
                            (obj, part) => obj && obj[part], window
                          );
                          invoke(fn);
                        }
                        invoke(window.smartCaptchaCallback);
                        invoke(window.onSmartCaptchaSuccess);
                        const form = input.closest('form') || document.querySelector('form');
                        if (form) {
                          const submitter = form.querySelector(
                            'button[type="submit"], input[type="submit"], button:not([type])'
                          );
                          if (submitter && !submitter.disabled) submitter.click();
                          else if (typeof form.requestSubmit === 'function') form.requestSubmit();
                          else form.submit();
                        }
                        return true;
                      }
                      const selectors = normalized === 'hcaptcha'
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
                      if (normalized === 'recaptcha_v3' || normalized === 'recaptcha-v3') {
                        let textarea = document.querySelector('textarea[name="g-recaptcha-response"]');
                        if (!textarea) {
                          textarea = document.createElement('textarea');
                          textarea.name = 'g-recaptcha-response';
                          textarea.style.display = 'none';
                          (document.forms[0] || document.body || document.documentElement).appendChild(textarea);
                        }
                        textarea.value = token;
                        textarea.dispatchEvent(new Event('input', {bubbles: true}));
                        textarea.dispatchEvent(new Event('change', {bubbles: true}));
                        changed = true;

                        const invoke = (fn) => {
                          if (typeof fn !== 'function') return false;
                          try { fn(token); return true; } catch (_) { return false; }
                        };
                        const seen = new Set();
                        const visit = (value) => {
                          if (!value || (typeof value !== 'object' && typeof value !== 'function')) return false;
                          if (seen.has(value)) return false;
                          seen.add(value);
                          let called = false;
                          if (typeof value === 'function') called = invoke(value) || called;
                          if (typeof value === 'object') {
                            for (const key of ['callback', 'promise-callback']) {
                              called = invoke(value[key]) || called;
                            }
                            for (const child of Object.values(value)) {
                              called = visit(child) || called;
                            }
                          }
                          return called;
                        };
                        const cfg = window.___grecaptcha_cfg;
                        if (cfg && cfg.clients) {
                          changed = visit(cfg.clients) || changed;
                        }
                        for (const el of document.querySelectorAll('[data-callback]')) {
                          const name = el.getAttribute('data-callback');
                          const fn = name && name.split('.').reduce((obj, part) => obj && obj[part], window);
                          changed = invoke(fn) || changed;
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


def _iter_domain_values(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple, set, frozenset)):
        values: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                values.append(text)
        return values
    return []


def _authorized_domains_from_config(config: dict[str, Any], settings: Any) -> frozenset[str]:
    """Union parser extras, bypass_config, and global captcha allowlists.

    Site parsers write ``captcha_authorized_domains`` into monitor/bypass
    config. The solver historically read only ``authorized_domains``, so
    SuperJob/Ashby/Higgsfield allowlists never reached the paid solver.
    """
    collected: list[str] = []
    for key in ("authorized_domains", "captcha_authorized_domains"):
        collected.extend(_iter_domain_values(config.get(key)))
    collected.extend(_iter_domain_values(getattr(settings, "captcha_authorized_domains", ())))
    normalized = [item.strip().lower().lstrip(".") for item in collected if item.strip()]
    if "*" in normalized:
        return frozenset({"*"})
    return frozenset(normalized)


def _create_captcha_solver(
    bypass_config: dict[str, Any] | None = None,
) -> CaptchaSolverBypass:
    config = bypass_config or {}
    from job_ftch.config import get_settings

    settings = get_settings()
    # Per-source bypass_config may override the active provider; otherwise the
    # global setting decides which external provider (if any) backs browser_wait.
    provider = str(config.get("provider", settings.captcha_provider))
    route_config = config.get("provider_routes", settings.captcha_provider_routes)
    provider_routes = _normalize_provider_routes(route_config)
    return CaptchaSolverBypass(
        provider=provider,
        api_key=_provider_api_key(provider),
        wait_seconds=float(config["wait_seconds"]) if "wait_seconds" in config else None,
        max_attempts=int(config.get("max_attempts", "2")),
        max_paid_attempts=int(config.get("max_paid_attempts", "1")),
        min_provider_seconds=float(
            config.get(
                "min_provider_seconds",
                settings.captcha_solver_timeout_budget_seconds,
            )
        ),
        backoff_seconds=float(
            config.get("backoff_seconds", settings.captcha_solver_backoff_seconds)
        ),
        proxy_url=str(config.get("proxy_url", "")),
        enabled_providers=frozenset(settings.captcha_enabled_providers),
        provider_routes=provider_routes,
        authorized_domains=_authorized_domains_from_config(config, settings),
    )


def _normalize_provider_routes(raw: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return {}
    routes: dict[str, tuple[str, ...]] = {}
    for challenge_type, providers in raw.items():
        normalized_type = normalize_challenge_type(str(challenge_type))
        if isinstance(providers, str):
            provider_list = tuple(item.strip() for item in providers.split(",") if item.strip())
        else:
            try:
                provider_list = tuple(str(item).strip() for item in providers if str(item).strip())
            except TypeError:
                provider_list = ()
        if provider_list:
            routes[normalized_type] = provider_list
    return routes


register_bypass(
    "captcha_solver",
    capability=BypassCapability(
        cost=50,
        browser_family="decorator",
        challenge_actions=frozenset({"captcha_solver"}),
    ),
)(_create_captcha_solver)
