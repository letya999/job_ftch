"""Self-registered paid CAPTCHA provider adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

from job_ftch.infrastructure.bypass.captcha_models import (
    CaptchaChallengeType,
    CaptchaFailureReason,
    CaptchaProviderCapability,
    CaptchaResultKind,
    CaptchaSolveResult,
)
from job_ftch.infrastructure.sources.source_deadline import sleep_with_source_deadline


class CaptchaProvider(Protocol):
    capability: CaptchaProviderCapability

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult: ...


_PROVIDER_FACTORIES: dict[str, type[CaptchaProvider]] = {}
_PROVIDER_CAPABILITIES: dict[str, CaptchaProviderCapability] = {}


def normalize_challenge_type(challenge_type: str) -> str:
    aliases = {
        "cloudflare": CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
        "cf_turnstile": CaptchaChallengeType.TURNSTILE.value,
        "cloudflare_turnstile": CaptchaChallengeType.TURNSTILE.value,
        "recaptcha3": CaptchaChallengeType.RECAPTCHA_V3.value,
        "recaptcha-v3": CaptchaChallengeType.RECAPTCHA_V3.value,
        "recaptcha_v3": CaptchaChallengeType.RECAPTCHA_V3.value,
    }
    normalized = aliases.get(challenge_type.strip().lower(), challenge_type.strip().lower())
    return normalized or CaptchaChallengeType.UNKNOWN.value


def _provider_wire_type(challenge_type: str) -> str:
    normalized = normalize_challenge_type(challenge_type)
    if normalized == CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value:
        return "cloudflare"
    return normalized


def register_captcha_provider(
    name: str,
) -> Callable[[type[CaptchaProvider]], type[CaptchaProvider]]:
    def _decorator(provider: type[CaptchaProvider]) -> type[CaptchaProvider]:
        _PROVIDER_FACTORIES[name] = provider
        capability = getattr(provider, "capability", None)
        if isinstance(capability, CaptchaProviderCapability):
            _PROVIDER_CAPABILITIES[name] = capability
        return provider

    return _decorator


def list_captcha_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_FACTORIES))


def get_captcha_provider_capability(name: str) -> CaptchaProviderCapability | None:
    return _PROVIDER_CAPABILITIES.get(name)


def list_captcha_provider_capabilities() -> tuple[CaptchaProviderCapability, ...]:
    return tuple(_PROVIDER_CAPABILITIES[name] for name in sorted(_PROVIDER_CAPABILITIES))


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
        explicit_sitekey = str(
            await page.evaluate(
                "(()=>{const el=document.querySelector('[data-sitekey]');"
                "return el?el.getAttribute('data-sitekey'):'';})()"
            )
        )
        if explicit_sitekey:
            return explicit_sitekey
    except Exception:
        pass
    try:
        return str(
            await page.evaluate(
                r"""(()=>{
                    const script=[...document.scripts].find((el)=>{
                        const src=el.getAttribute('src')||'';
                        return src.includes('recaptcha/api.js') && src.includes('render=');
                    });
                    if(!script) return '';
                    try {
                        const src=script.getAttribute('src')||'';
                        const url=new URL(src, document.baseURI);
                        const key=url.searchParams.get('render')||'';
                        return key === 'explicit' ? '' : key;
                    } catch {
                        const m=(script.getAttribute('src')||'').match(/[?&]render=([^&]+)/);
                        return m ? decodeURIComponent(m[1]) : '';
                    }
                })()"""
            )
        )
    except Exception:
        return ""


async def extract_recaptcha_action(page: Any) -> str:
    if not hasattr(page, "evaluate"):
        return ""
    try:
        captured = str(
            await page.evaluate(
                r"""(()=>{
                    const calls=window.__job_ftch_recaptcha_executes || [];
                    const last=[...calls].reverse().find((call)=>call && call.action);
                    return last ? last.action : '';
                })()"""
            )
        )
        if captured:
            return captured
    except Exception:
        pass
    try:
        return str(
            await page.evaluate(
                r"""(()=>{
                    const html=document.documentElement.outerHTML || '';
                    const match=html.match(/grecaptcha\.execute\([^)]*action\s*:\s*['"]([^'"]+)['"]/i);
                    return match ? match[1] : '';
                })()"""
            )
        )
    except Exception:
        return ""


class _BaseProvider:
    supported = frozenset(
        {
            CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
            CaptchaChallengeType.HCAPTCHA.value,
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.RECAPTCHA_V3.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="base",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
    )

    def __init__(self, api_key: str, *, proxy_url: str = "") -> None:
        self.api_key = api_key
        self.proxy_url = proxy_url

    def unsupported(self, challenge_type: str, method: str) -> CaptchaSolveResult | None:
        if normalize_challenge_type(challenge_type) in self.supported:
            return None
        return CaptchaSolveResult(
            solved=False,
            method=method,
            error=f"unsupported challenge: {challenge_type}",
            failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
            challenge_type=normalize_challenge_type(challenge_type),
            result_kind=CaptchaResultKind.UNSUPPORTED,
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
    capability = CaptchaProviderCapability(
        provider="capsolver",
        supported_challenge_types=frozenset(
            {
                CaptchaChallengeType.RECAPTCHA.value,
                CaptchaChallengeType.RECAPTCHA_V3.value,
                CaptchaChallengeType.HCAPTCHA.value,
                CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
            }
        ),
        result_kinds=frozenset({CaptchaResultKind.TOKEN, CaptchaResultKind.SESSION}),
        production_candidate=True,
        browser_context_required=True,
        notes="Primary production candidate for recaptcha; Cloudflare challenge remains experimental.",
    )

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
        wire_type = _provider_wire_type(challenge_type)
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="capsolver",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        effective_proxy = proxy_url or self.proxy_url
        if effective_proxy:
            task_type = {
                "hcaptcha": "HCaptchaTask",
                "recaptcha": "ReCaptchaV2Task",
                "recaptcha_v3": "ReCaptchaV3Task",
                "cloudflare": "AntiCloudflareTask",
            }[wire_type]
        else:
            task_type = {
                "hcaptcha": "HCaptchaTaskProxyLess",
                "recaptcha": "ReCaptchaV2TaskProxyLess",
                "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
                "cloudflare": "AntiCloudflareTask",
            }[wire_type]
        task_payload: dict[str, Any] = {
            "type": task_type,
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value:
            task_payload["pageAction"] = await extract_recaptcha_action(page) or "homepage"
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
                            challenge_type=challenge_type,
                            task_id=str(task_id),
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
    supported = frozenset(
        {
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.HCAPTCHA.value,
            CaptchaChallengeType.TURNSTILE.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="2captcha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        benchmark_candidate=True,
        notes="Long-tail fallback candidate; not enabled by default.",
    )

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
        wire_type = _provider_wire_type(challenge_type)
        site_key = await extract_sitekey(page)
        task_info = {
            "cloudflare": ("turnstile", "sitekey"),
            "turnstile": ("turnstile", "sitekey"),
            "hcaptcha": ("hcaptcha", "sitekey"),
            "recaptcha": ("userrecaptcha", "googlekey"),
        }[wire_type]
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
                        return _token_result(
                            "2captcha",
                            result.get("request", ""),
                            challenge_type=challenge_type,
                            task_id=str(submitted["request"]),
                        )
                    if result.get("request") != "CAPCHA_NOT_READY":
                        return _rejected("2captcha", str(result.get("request", "failed")))
                return _timeout("2captcha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("2captcha", exc)


@register_captcha_provider("anticaptcha")
class AntiCaptchaProvider(_BaseProvider):
    supported = frozenset(
        {
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.HCAPTCHA.value,
            CaptchaChallengeType.TURNSTILE.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="anticaptcha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        notes="Dormant fallback; keep registered for compatibility, not a current priority.",
    )

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
        wire_type = _provider_wire_type(challenge_type)
        site_key = await extract_sitekey(page)
        effective_proxy = proxy_url or self.proxy_url
        if effective_proxy:
            task_type = {
                "cloudflare": "TurnstileTask",
                "turnstile": "TurnstileTask",
                "hcaptcha": "HCaptchaTask",
                "recaptcha": "RecaptchaV2Task",
            }[wire_type]
        else:
            task_type = {
                "cloudflare": "TurnstileTaskProxyless",
                "turnstile": "TurnstileTaskProxyless",
                "hcaptcha": "HCaptchaTaskProxyless",
                "recaptcha": "RecaptchaV2TaskProxyless",
            }[wire_type]
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
                            challenge_type=challenge_type,
                            task_id=str(created["taskId"]),
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

    supported = frozenset({CaptchaChallengeType.RECAPTCHA.value, CaptchaChallengeType.HCAPTCHA.value})
    capability = CaptchaProviderCapability(
        provider="nopecha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        free_or_dev=True,
        notes="Free/dev provider, not a production default.",
    )
    _TYPE_MAP = {"hcaptcha": "hcaptcha", "recaptcha": "recaptcha2"}

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
        wire_type = _provider_wire_type(challenge_type)
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="nopecha",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        endpoint = f"https://api.nopecha.com/v1/token/{self._TYPE_MAP[wire_type]}"
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
                        return _token_result(
                            "nopecha",
                            poll.json().get("data", ""),
                            challenge_type=challenge_type,
                            task_id=str(job_id),
                        )
                    return _rejected("nopecha", f"poll failed: HTTP {poll.status_code}")
                return _timeout("nopecha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("nopecha", exc)


@register_captcha_provider("capmonster")
class CapMonsterProvider(_BaseProvider):
    supported = frozenset(
        {
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.RECAPTCHA_V3.value,
            CaptchaChallengeType.TURNSTILE.value,
            CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="capmonster",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN, CaptchaResultKind.SESSION}),
        production_candidate=True,
        browser_context_required=True,
        notes="Second production candidate; Cloudflare challenge route is experimental.",
    )

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "capmonster"):
            return unsupported
        wire_type = _provider_wire_type(challenge_type)
        if wire_type == "cloudflare":
            return CaptchaSolveResult(
                solved=False,
                method="capmonster",
                error="cloudflare challenge requires browser-derived task parameters",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="capmonster",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        effective_proxy = proxy_url or self.proxy_url
        task_type = {
            "recaptcha": "RecaptchaV2Task",
            "recaptcha_v3": "RecaptchaV3TaskProxyless",
            "turnstile": "TurnstileTask",
        }[wire_type]
        task_payload: dict[str, Any] = {
            "type": task_type,
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value:
            task_payload["pageAction"] = await extract_recaptcha_action(page) or "homepage"
            task_payload["minScore"] = 0.3
        if effective_proxy and wire_type != CaptchaChallengeType.RECAPTCHA_V3.value:
            task_payload.update(self._proxy_task_fields())
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = (
                    await client.post(
                        "https://api.capmonster.cloud/createTask",
                        json={"clientKey": self.api_key, "task": task_payload},
                    )
                ).json()
                if created.get("errorId", 0) != 0 or not created.get("taskId"):
                    return _rejected(
                        "capmonster",
                        str(created.get("errorDescription", "createTask failed")),
                    )
                task_id = str(created["taskId"])
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.post(
                            "https://api.capmonster.cloud/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": created["taskId"]},
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution", {})
                        token = (
                            solution.get("gRecaptchaResponse")
                            or solution.get("token")
                            or solution.get("cf_clearance")
                        )
                        return _token_result(
                            "capmonster",
                            token,
                            challenge_type=challenge_type,
                            task_id=task_id,
                        )
                    if result.get("errorId", 0) != 0:
                        return _rejected(
                            "capmonster",
                            str(result.get("errorDescription", "failed")),
                        )
                return _timeout("capmonster")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("capmonster", exc)


@register_captcha_provider("nextcaptcha")
class NextCaptchaProvider(_BaseProvider):
    supported = frozenset(
        {
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.RECAPTCHA_V3.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="nextcaptcha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        benchmark_candidate=True,
        notes="Cheap benchmark candidate for reCAPTCHA only.",
    )

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        if unsupported := self.unsupported(challenge_type, "nextcaptcha"):
            return unsupported
        wire_type = _provider_wire_type(challenge_type)
        site_key = await extract_sitekey(page)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="nextcaptcha",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        effective_proxy = proxy_url or self.proxy_url
        task_payload: dict[str, Any] = {
            "type": (
                "RecaptchaV3TaskProxyless"
                if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value and not effective_proxy
                else "RecaptchaV3Task"
                if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value
                else "RecaptchaV2TaskProxyless"
                if not effective_proxy
                else "RecaptchaV2Task"
            ),
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value:
            task_payload["pageAction"] = await extract_recaptcha_action(page) or "homepage"
        if effective_proxy:
            task_payload["proxy"] = effective_proxy
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = (
                    await client.post(
                        "https://api.nextcaptcha.com/createTask",
                        json={"clientKey": self.api_key, "task": task_payload},
                    )
                ).json()
                if created.get("errorId", 0) != 0 or not created.get("taskId"):
                    return _rejected(
                        "nextcaptcha",
                        str(created.get("errorDescription", "createTask failed")),
                    )
                task_id = str(created["taskId"])
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.post(
                            "https://api.nextcaptcha.com/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": created["taskId"]},
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution", {})
                        return _token_result(
                            "nextcaptcha",
                            solution.get("gRecaptchaResponse"),
                            challenge_type=challenge_type,
                            task_id=task_id,
                        )
                    if result.get("errorId", 0) != 0:
                        return _rejected(
                            "nextcaptcha",
                            str(result.get("errorDescription", "failed")),
                        )
                return _timeout("nextcaptcha")
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("nextcaptcha", exc)


def _token_result(
    method: str,
    token: Any,
    *,
    challenge_type: str = CaptchaChallengeType.UNKNOWN.value,
    task_id: str = "",
) -> CaptchaSolveResult:
    value = str(token or "")
    if not value:
        return CaptchaSolveResult(
            solved=False,
            method=method,
            error="provider returned an empty token",
            failure_reason=CaptchaFailureReason.BAD_TOKEN,
            challenge_type=normalize_challenge_type(challenge_type),
            result_kind=CaptchaResultKind.TOKEN,
            provider_task_id=task_id,
        )
    return CaptchaSolveResult(
        solved=True,
        method=method,
        tokens={"captcha_token": value},
        challenge_type=normalize_challenge_type(challenge_type),
        result_kind=CaptchaResultKind.TOKEN,
        provider_task_id=task_id,
    )


def _rejected(method: str, error: str) -> CaptchaSolveResult:
    safe_error = str(error)[:240]
    return CaptchaSolveResult(
        solved=False,
        method=method,
        error=safe_error,
        failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
        raw_provider_status=safe_error,
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
