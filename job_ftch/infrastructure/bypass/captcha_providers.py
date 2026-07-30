"""Self-registered paid CAPTCHA provider adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

from job_ftch.infrastructure.bypass.captcha_models import (
    CaptchaFailureReason,
    CaptchaSolveResult,
)
from job_ftch.infrastructure.sources.source_deadline import sleep_with_source_deadline


class CaptchaProvider(Protocol):
    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult: ...


_PROVIDER_FACTORIES: dict[str, type[CaptchaProvider]] = {}


def register_captcha_provider(
    name: str,
) -> Callable[[type[CaptchaProvider]], type[CaptchaProvider]]:
    def _decorator(provider: type[CaptchaProvider]) -> type[CaptchaProvider]:
        _PROVIDER_FACTORIES[name] = provider
        return provider

    return _decorator


def resolve_captcha_provider(
    name: str,
    api_key: str,
    *,
    proxy_url: str = "",
) -> CaptchaProvider:
    try:
        provider = _PROVIDER_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown CAPTCHA provider: {name}") from exc
    try:
        return provider(api_key, proxy_url=proxy_url)  # type: ignore[call-arg]
    except TypeError:
        return provider(api_key)  # type: ignore[call-arg]


async def extract_sitekey(page: Any) -> str:
    if not hasattr(page, "evaluate"):
        return ""
    try:
        return str(
            await page.evaluate(
                "(()=>{const el=document.querySelector('[data-sitekey]');"
                "return el?el.getAttribute('data-sitekey'):'';})()"
            )
        )
    except Exception:
        return ""


class _BaseProvider:
    supported = frozenset({"cloudflare", "hcaptcha", "recaptcha"})

    def __init__(self, api_key: str, *, proxy_url: str = "") -> None:
        self.api_key = api_key
        self.proxy_url = proxy_url

    def unsupported(self, challenge_type: str, method: str) -> CaptchaSolveResult | None:
        if challenge_type in self.supported:
            return None
        return CaptchaSolveResult(
            solved=False,
            method=method,
            error=f"unsupported challenge: {challenge_type}",
            failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
        )

    def _proxy_task_fields(self) -> dict[str, Any]:
        """Build proxy fields for provider task payloads when proxy_url is set."""
        if not self.proxy_url:
            return {}
        from urllib.parse import urlparse as _urlparse

        parsed = _urlparse(self.proxy_url)
        # CapSolver / AntiCaptcha expect a lowercase proxyType
        # ("http" | "https" | "socks4" | "socks5").
        fields: dict[str, Any] = {
            "proxyType": (parsed.scheme.lower() or "http"),
            "proxyAddress": parsed.hostname or "",
            "proxyPort": parsed.port or 0,
        }
        if parsed.username:
            fields["proxyLogin"] = parsed.username
        if parsed.password:
            fields["proxyPassword"] = parsed.password
        return fields


@register_captcha_provider("capsolver")
class CapSolverProvider(_BaseProvider):
    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "capsolver"):
            return unsupported
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="capsolver",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
            )
        effective_proxy = proxy_url or self.proxy_url
        if effective_proxy:
            task_type = {
                "hcaptcha": "HCaptchaTask",
                "recaptcha": "ReCaptchaV2Task",
                "cloudflare": "AntiCloudflareTask",
            }[challenge_type]
        else:
            task_type = {
                "hcaptcha": "HCaptchaTaskProxyLess",
                "recaptcha": "ReCaptchaV2TaskProxyLess",
                "cloudflare": "AntiCloudflareTask",
            }[challenge_type]
        task_payload: dict[str, Any] = {
            "type": task_type,
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if effective_proxy:
            task_payload.update(self._proxy_task_fields())
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = (
                    await client.post(
                        "https://api.capsolver.com/createTask",
                        json={
                            "clientKey": self.api_key,
                            "task": task_payload,
                        },
                    )
                ).json()
                task_id = created.get("taskId")
                if not task_id:
                    return _rejected("capsolver", "provider rejected createTask")
                for _ in range(30):
                    await sleep_with_source_deadline(2.0)
                    result = (
                        await client.post(
                            "https://api.capsolver.com/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": task_id},
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution", {})
                        return _token_result(
                            "capsolver",
                            solution.get("gRecaptchaResponse") or solution.get("token", ""),
                        )
                    if result.get("status") == "failed":
                        return _rejected(
                            "capsolver",
                            result.get("errorDescription", "provider failed"),
                        )
                return _timeout("capsolver")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return _unavailable("capsolver", exc)


@register_captcha_provider("2captcha")
class TwoCaptchaProvider(_BaseProvider):
    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "2captcha"):
            return unsupported
        site_key = await extract_sitekey(page)
        task_info = {
            "cloudflare": ("turnstile", "sitekey"),
            "hcaptcha": ("hcaptcha", "sitekey"),
            "recaptcha": ("userrecaptcha", "googlekey"),
        }[challenge_type]
        effective_proxy = proxy_url or self.proxy_url
        try:
            submit_data: dict[str, Any] = {
                "key": self.api_key,
                "method": task_info[0],
                task_info[1]: site_key,
                "pageurl": url or str(getattr(page, "url", "")),
                "json": "1",
            }
            if effective_proxy:
                submit_data["proxy"] = effective_proxy
                submit_data["proxytype"] = "HTTP"
            async with httpx.AsyncClient(timeout=30.0) as client:
                submitted = (
                    await client.post(
                        "https://2captcha.com/in.php",
                        data=submit_data,
                    )
                ).json()
                if submitted.get("status") != 1:
                    return _rejected("2captcha", str(submitted.get("request", "submit failed")))
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.get(
                            "https://2captcha.com/res.php",
                            params={
                                "key": self.api_key,
                                "action": "get",
                                "id": submitted["request"],
                                "json": "1",
                            },
                        )
                    ).json()
                    if result.get("status") == 1:
                        return _token_result("2captcha", result.get("request", ""))
                    if result.get("request") != "CAPCHA_NOT_READY":
                        return _rejected("2captcha", str(result.get("request", "failed")))
                return _timeout("2captcha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("2captcha", exc)


@register_captcha_provider("anticaptcha")
class AntiCaptchaProvider(_BaseProvider):
    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "anticaptcha"):
            return unsupported
        site_key = await extract_sitekey(page)
        effective_proxy = proxy_url or self.proxy_url
        if effective_proxy:
            task_type = {
                "cloudflare": "TurnstileTask",
                "hcaptcha": "HCaptchaTask",
                "recaptcha": "RecaptchaV2Task",
            }[challenge_type]
        else:
            task_type = {
                "cloudflare": "TurnstileTaskProxyless",
                "hcaptcha": "HCaptchaTaskProxyless",
                "recaptcha": "RecaptchaV2TaskProxyless",
            }[challenge_type]
        task_payload: dict[str, Any] = {
            "type": task_type,
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if effective_proxy:
            task_payload.update(self._proxy_task_fields())
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = (
                    await client.post(
                        "https://api.anti-captcha.com/createTask",
                        json={
                            "clientKey": self.api_key,
                            "task": task_payload,
                        },
                    )
                ).json()
                if created.get("errorId", 0) != 0 or not created.get("taskId"):
                    return _rejected(
                        "anticaptcha",
                        str(created.get("errorDescription", "createTask failed")),
                    )
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.post(
                            "https://api.anti-captcha.com/getTaskResult",
                            json={
                                "clientKey": self.api_key,
                                "taskId": created["taskId"],
                            },
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution", {})
                        return _token_result(
                            "anticaptcha",
                            solution.get("gRecaptchaResponse")
                            or solution.get("token")
                            or solution.get("cf_clearance", ""),
                        )
                    if result.get("errorId", 0) != 0:
                        return _rejected(
                            "anticaptcha",
                            str(result.get("errorDescription", "failed")),
                        )
                return _timeout("anticaptcha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("anticaptcha", exc)


@register_captcha_provider("nopecha")
class NopeChaProvider(_BaseProvider):
    """Free-tier provider (recurring free credits every 23h, no card required)."""

    _TYPE_MAP = {"hcaptcha": "hcaptcha", "recaptcha": "recaptcha2", "cloudflare": "turnstile"}

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "nopecha"):
            return unsupported
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="nopecha",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
            )
        endpoint = f"https://api.nopecha.com/v1/token/{self._TYPE_MAP[challenge_type]}"
        headers = {"Authorization": f"Basic {self.api_key}"}
        page_url = url or str(getattr(page, "url", ""))
        effective_proxy = proxy_url or self.proxy_url
        try:
            payload: dict[str, Any] = {"sitekey": site_key, "url": page_url}
            if effective_proxy:
                payload["proxy"] = {"url": effective_proxy}
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                submitted = await client.post(
                    endpoint,
                    json=payload,
                )
                if submitted.status_code != 200:
                    return _rejected("nopecha", f"submit failed: HTTP {submitted.status_code}")
                job_id = submitted.json().get("data")
                if not job_id:
                    return _rejected("nopecha", "provider did not return a job id")
                for _ in range(30):
                    await sleep_with_source_deadline(1.0)
                    poll = await client.get(endpoint, params={"id": job_id})
                    if poll.status_code == 409:
                        continue
                    if poll.status_code == 200:
                        return _token_result("nopecha", poll.json().get("data", ""))
                    return _rejected("nopecha", f"poll failed: HTTP {poll.status_code}")
                return _timeout("nopecha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("nopecha", exc)


def _token_result(method: str, token: Any) -> CaptchaSolveResult:
    value = str(token or "")
    if not value:
        return CaptchaSolveResult(
            solved=False,
            method=method,
            error="provider returned an empty token",
            failure_reason=CaptchaFailureReason.BAD_TOKEN,
        )
    return CaptchaSolveResult(solved=True, method=method, tokens={"captcha_token": value})


def _rejected(method: str, error: str) -> CaptchaSolveResult:
    return CaptchaSolveResult(
        solved=False,
        method=method,
        error=error,
        failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
    )


def _timeout(method: str) -> CaptchaSolveResult:
    return CaptchaSolveResult(
        solved=False,
        method=method,
        error="timeout waiting for solution",
        failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
    )


def _unavailable(method: str, exc: Exception) -> CaptchaSolveResult:
    return CaptchaSolveResult(
        solved=False,
        method=method,
        error=type(exc).__name__,
        failure_reason=CaptchaFailureReason.PROVIDER_UNAVAILABLE,
    )
