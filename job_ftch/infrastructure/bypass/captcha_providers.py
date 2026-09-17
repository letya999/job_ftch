"""Self-registered paid CAPTCHA provider adapters."""

from __future__ import annotations

import base64
import json
import re
import socket
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

import httpx
import structlog

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

logger = structlog.get_logger("job_ftch.bypass.captcha")


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
        "yandex": CaptchaChallengeType.SMARTCAPTCHA.value,
        "yandex_smartcaptcha": CaptchaChallengeType.SMARTCAPTCHA.value,
        "yandexsmartcaptcha": CaptchaChallengeType.SMARTCAPTCHA.value,
        "showcaptcha": CaptchaChallengeType.SMARTCAPTCHA.value,
    }
    normalized = aliases.get(challenge_type.strip().lower(), challenge_type.strip().lower())
    return normalized or CaptchaChallengeType.UNKNOWN.value


def _provider_wire_type(challenge_type: str) -> str:
    normalized = normalize_challenge_type(challenge_type)
    if normalized == CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value:
        return "cloudflare"
    return normalized


async def _page_user_agent(page: Any) -> str:
    if not hasattr(page, "evaluate"):
        return ""
    try:
        return str(await page.evaluate("navigator.userAgent") or "").strip()
    except Exception:
        return ""


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
        yandex_client_key = str(
            await page.evaluate(
                "(()=>{const el=document.querySelector('[data-sitekey^=\"ysc1_\"]');"
                "return el?el.getAttribute('data-sitekey'):'';})()"
            )
        )
        if yandex_client_key:
            return yandex_client_key
    except Exception:
        pass
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
        yandex_sitekey = str(
            await page.evaluate(
                r"""(()=>{
                    const iframe=[...document.querySelectorAll('iframe[src]')].find((el)=>{
                        const src=el.getAttribute('src')||'';
                        return src.includes('smartcaptcha') || src.includes('ysc1_');
                    });
                    if(!iframe) return '';
                    try {
                        return new URL(iframe.src, document.baseURI).searchParams.get('sitekey')||'';
                    } catch {
                        const m=(iframe.getAttribute('src')||'').match(/[?&]sitekey=([^&]+)/);
                        return m ? decodeURIComponent(m[1]) : '';
                    }
                })()"""
            )
        )
        if yandex_sitekey:
            return yandex_sitekey
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


async def extract_turnstile_metadata(page: Any) -> dict[str, str]:
    """Read optional public Turnstile widget metadata from the rendered DOM."""
    if not hasattr(page, "evaluate"):
        return {}
    try:
        raw = await page.evaluate(
            """(()=>{
                const el=document.querySelector('.cf-turnstile,[data-sitekey]');
                if(!el) return {};
                return {
                    action: el.getAttribute('data-action') || '',
                    cdata: el.getAttribute('data-cdata') || ''
                };
            })()"""
        )
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw[key])
        for key in ("action", "cdata")
        if isinstance(raw.get(key), str) and raw[key]
    }


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

    async def _extract_image_with_wait(self, page: Any) -> str:
        body = await page.evaluate(
            """async () => {
                const selector = 'img[data-qa*="captcha"], img[alt="captcha" i], img[class*="captcha-image"], img[class*="captcha_image"], img[src*="captcha"]';
                const selectImage = () => [...document.querySelectorAll(selector)].find(img => img.getClientRects().length);
                const original = selectImage();
                const form = original && original.closest('form');
                const english = form && [...form.querySelectorAll('button')].find(button =>
                    /^English$/i.test(button.innerText.trim()) && !button.disabled
                );
                if (english) {
                    const previous = original.currentSrc;
                    english.click();
                    let changed = false;
                    for (let attempt = 0; attempt < 20; attempt++) {
                        await new Promise(resolve => setTimeout(resolve, 250));
                        const next = selectImage();
                        if (next && next.currentSrc && next.currentSrc !== previous && next.complete && next.naturalWidth) {
                            changed = true;
                            break;
                        }
                    }
                    if (!changed) return '';
                }
                const img = selectImage();
                if (!img) return '';
                for (let attempt = 0; attempt < 20 && (!img.currentSrc || !img.complete || !img.naturalWidth); attempt++) {
                    await new Promise(resolve => setTimeout(resolve, 250));
                }
                if (!img.currentSrc || !img.complete || !img.naturalWidth) return '';
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                canvas.getContext('2d').drawImage(img, 0, 0);
                const body = canvas.toDataURL('image/png').split(',')[1] || '';
                return body.length <= 2700000 ? body : '';
            }"""
        )
        return body if isinstance(body, str) else ""

    async def _extract_sitekey_with_wait(self, page: Any, challenge_type: str) -> str:
        await _wait_for_sitekey_marker(page, challenge_type)
        return await extract_sitekey(page)

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

    def _capsolver_proxy_value(self) -> str:
        """Return CapSolver's compact sticky proxy format.

        CapSolver's Cloudflare Challenge API expects one ``proxy`` string
        (``host:port`` or ``host:port:user:pass``), not the split proxy fields
        used by their Turnstile/reCAPTCHA task types.
        """
        if not self.proxy_url:
            return ""
        parsed = urlparse(self.proxy_url)
        if not parsed.hostname or not parsed.port:
            return self.proxy_url
        host = _resolve_proxy_host_for_provider(parsed.hostname)
        value = f"{host}:{parsed.port}"
        if parsed.username:
            value = f"{value}:{parsed.username}:{parsed.password or ''}"
        return value

    async def _page_user_agent(self, page: Any) -> str:
        if not hasattr(page, "evaluate"):
            return ""
        try:
            return str(await page.evaluate("navigator.userAgent"))
        except Exception:
            return ""

    async def _page_html(self, page: Any, *, limit: int = 250_000) -> str:
        content = getattr(page, "content", None)
        if not callable(content):
            return ""
        try:
            return str(await content())[:limit]
        except Exception:
            return ""


async def _wait_for_sitekey_marker(
    page: Any,
    challenge_type: str,
    *,
    timeout_ms: int = 3000,
) -> None:
    wait_for_selector = getattr(page, "wait_for_selector", None)
    if not callable(wait_for_selector):
        return
    normalized = normalize_challenge_type(challenge_type)
    selectors = {
        CaptchaChallengeType.TURNSTILE.value: (
            ".cf-turnstile",
            "#turnstile-wrapper",
            "[data-sitekey]",
            "iframe[src*='turnstile']",
        ),
        CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value: (
            ".cf-turnstile",
            "#challenge-stage",
            "[data-sitekey]",
            "iframe[src*='turnstile']",
        ),
        CaptchaChallengeType.HCAPTCHA.value: (
            ".h-captcha",
            "[data-sitekey]",
            "iframe[src*='hcaptcha.com']",
        ),
        CaptchaChallengeType.RECAPTCHA.value: (
            ".g-recaptcha",
            "[data-sitekey]",
            "iframe[src*='recaptcha']",
        ),
        CaptchaChallengeType.RECAPTCHA_V3.value: (
            "script[src*='recaptcha/api.js']",
            "[data-sitekey]",
        ),
        CaptchaChallengeType.SMARTCAPTCHA.value: (
            ".smart-captcha",
            "#smartcaptcha-container",
            '[data-sitekey^="ysc1_"]',
            "iframe[src*='smartcaptcha']",
            'input[name="smart-token"]',
        ),
    }.get(normalized, ("[data-sitekey]",))
    for selector in selectors:
        try:
            await wait_for_selector(selector, timeout=timeout_ms)
            return
        except Exception:
            continue


@register_captcha_provider("capsolver")
class CapSolverProvider(_BaseProvider):
    supported = frozenset(
        {
            CaptchaChallengeType.RECAPTCHA.value,
            CaptchaChallengeType.RECAPTCHA_V3.value,
            CaptchaChallengeType.HCAPTCHA.value,
            CaptchaChallengeType.TURNSTILE.value,
            CaptchaChallengeType.CLOUDFLARE_CHALLENGE.value,
            CaptchaChallengeType.SMARTCAPTCHA.value,
            CaptchaChallengeType.IMAGE.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="capsolver",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN, CaptchaResultKind.SESSION}),
        production_candidate=True,
        browser_context_required=True,
        notes=(
            "Primary production candidate for reCAPTCHA, Turnstile, and "
            "Yandex SmartCaptcha; managed Cloudflare challenge remains experimental."
        ),
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
        if wire_type == CaptchaChallengeType.IMAGE.value:
            try:
                body = await self._extract_image_with_wait(page)
                if not isinstance(body, str) or not body:
                    return _rejected("capsolver", "captcha image could not be extracted")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.capsolver.com/createTask",
                        json={
                            "clientKey": self.api_key,
                            "task": {
                                "type": "ImageToTextTask",
                                "websiteURL": url or str(getattr(page, "url", "")),
                                "module": "common",
                                "body": body,
                            },
                        },
                    )
                    response.raise_for_status()
                    created = response.json()
                if created.get("errorId", 0) != 0 or created.get("status") != "ready":
                    return _rejected(
                        "capsolver", _provider_error_text(created, "image recognition failed")
                    )
                solution = created.get("solution") or {}
                text = solution.get("text") if isinstance(solution, dict) else None
                return _token_result(
                    "capsolver",
                    text.strip() if isinstance(text, str) else "",
                    challenge_type=challenge_type,
                    task_id=str(created.get("taskId") or ""),
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                return _unavailable("capsolver", exc)
        if wire_type == "cloudflare":
            proxy_value = self._capsolver_proxy_value()
            if not proxy_value:
                return CaptchaSolveResult(
                    solved=False,
                    method="capsolver",
                    error="cloudflare challenge requires a sticky/static proxy",
                    failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                    challenge_type=normalize_challenge_type(challenge_type),
                    result_kind=CaptchaResultKind.UNSUPPORTED,
                )
            cloudflare_task_payload: dict[str, Any] = {
                "type": "AntiCloudflareTask",
                "websiteURL": url or str(getattr(page, "url", "")),
                "proxy": proxy_value,
            }
            user_agent = await self._page_user_agent(page)
            if user_agent:
                cloudflare_task_payload["userAgent"] = user_agent
            challenge_html = await self._page_html(page)
            if challenge_html:
                cloudflare_task_payload["html"] = challenge_html
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    created = (
                        await client.post(
                            "https://api.capsolver.com/createTask",
                            json={
                                "clientKey": self.api_key,
                                "task": cloudflare_task_payload,
                            },
                        )
                    ).json()
                    task_id = created.get("taskId")
                    if not task_id:
                        return _rejected(
                            "capsolver",
                            _provider_error_text(created, "provider rejected createTask"),
                        )
                    for _ in range(60):
                        await sleep_with_source_deadline(1.0)
                        result = (
                            await client.post(
                                "https://api.capsolver.com/getTaskResult",
                                json={"clientKey": self.api_key, "taskId": task_id},
                            )
                        ).json()
                        if result.get("status") == "ready":
                            cookies = _extract_solution_cookies(result.get("solution", {}))
                            if cookies:
                                return _session_result(
                                    "capsolver",
                                    cookies,
                                    challenge_type=challenge_type,
                                    task_id=str(task_id),
                                )
                            return _rejected(
                                "capsolver",
                                "provider returned no clearance cookie",
                            )
                        if result.get("status") == "failed":
                            return _rejected(
                                "capsolver",
                                _provider_error_text(result, "provider failed"),
                            )
                    return _timeout("capsolver")
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                return _unavailable("capsolver", exc)
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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
            task_types = {
                "hcaptcha": ("HCaptchaTask",),
                "recaptcha": ("ReCaptchaV2Task",),
                "recaptcha_v3": ("ReCaptchaV3Task",),
                "turnstile": ("AntiTurnstileTask",),
                "cloudflare": ("AntiCloudflareTask",),
                "smartcaptcha": (
                    "YandexCaptchaTask",
                    "YandexSmartCaptchaTask",
                    "YandexCaptchaTaskProxyLess",
                    "YandexSmartCaptchaTaskProxyLess",
                    "YandexSmartCaptchaTaskProxyless",
                ),
            }[wire_type]
        else:
            task_types = {
                "hcaptcha": ("HCaptchaTaskProxyLess",),
                "recaptcha": ("ReCaptchaV2TaskProxyLess",),
                "recaptcha_v3": ("ReCaptchaV3TaskProxyLess",),
                "turnstile": ("AntiTurnstileTaskProxyLess",),
                "cloudflare": ("AntiCloudflareTask",),
                "smartcaptcha": (
                    "YandexCaptchaTaskProxyLess",
                    "YandexSmartCaptchaTaskProxyLess",
                    "YandexSmartCaptchaTaskProxyless",
                ),
            }[wire_type]
        page_url = str(getattr(page, "url", "") or url or "")
        task_payload: dict[str, Any] = {
            "type": task_types[0],
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value:
            action = await extract_recaptcha_action(page)
            if action:
                task_payload["pageAction"] = action
            task_payload["minScore"] = 0.3
        elif wire_type == CaptchaChallengeType.TURNSTILE.value:
            metadata = await extract_turnstile_metadata(page)
            if metadata:
                task_payload["metadata"] = metadata
        elif wire_type == CaptchaChallengeType.SMARTCAPTCHA.value:
            user_agent = await _page_user_agent(page)
            if user_agent:
                task_payload["userAgent"] = user_agent
        if effective_proxy:
            task_payload.update(self._proxy_task_fields())
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = None
                task_id = None
                last_reject = None
                reject_notes: list[str] = []
                proxy_fields = self._proxy_task_fields() if effective_proxy else {}
                for task_type in task_types:
                    task_payload["type"] = task_type
                    proxyless = "ProxyLess" in task_type or "Proxyless" in task_type
                    for key in (
                        "proxyType",
                        "proxyAddress",
                        "proxyPort",
                        "proxyLogin",
                        "proxyPassword",
                    ):
                        task_payload.pop(key, None)
                    if effective_proxy and not proxyless:
                        task_payload.update(proxy_fields)
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
                    if task_id:
                        break
                    last_reject = created
                    reject_notes.append(
                        f"{task_type}:{_provider_error_text(created, 'rejected')}"
                    )
                if not task_id:
                    return _rejected(
                        "capsolver",
                        ";".join(reject_notes)
                        or _provider_error_text(
                            last_reject or created, "provider rejected createTask"
                        ),
                    )
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
                            _provider_error_text(result, "provider failed"),
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
            CaptchaChallengeType.SMARTCAPTCHA.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="2captcha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        benchmark_candidate=True,
        notes="Long-tail fallback; documents Yandex SmartCaptcha token tasks.",
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
        if wire_type == CaptchaChallengeType.SMARTCAPTCHA.value:
            return await self._solve_yandex_token(
                page, challenge_type=challenge_type, url=url, proxy_url=proxy_url
            )
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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

    async def _solve_yandex_token(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str,
    ) -> CaptchaSolveResult:
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
        if not site_key:
            return CaptchaSolveResult(
                solved=False,
                method="2captcha",
                error="no sitekey found on page",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        effective_proxy = proxy_url or self.proxy_url
        page_url = str(getattr(page, "url", "") or url or "")
        task_payload: dict[str, Any] = {
            "type": (
                "YandexSmartCaptchaTask" if effective_proxy else "YandexSmartCaptchaTaskProxyless"
            ),
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        user_agent = await _page_user_agent(page)
        if user_agent:
            task_payload["userAgent"] = user_agent
        if effective_proxy:
            task_payload.update(self._proxy_task_fields())
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = (
                    await client.post(
                        "https://api.2captcha.com/createTask",
                        json={"clientKey": self.api_key, "task": task_payload},
                    )
                ).json()
                task_id = created.get("taskId")
                if created.get("errorId", 0) != 0 or not task_id:
                    return _rejected(
                        "2captcha",
                        str(
                            created.get("errorDescription")
                            or created.get("errorCode")
                            or "createTask failed"
                        ),
                    )
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.post(
                            "https://api.2captcha.com/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": task_id},
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution") or {}
                        return _token_result(
                            "2captcha",
                            solution.get("token") if isinstance(solution, dict) else "",
                            challenge_type=challenge_type,
                            task_id=str(task_id),
                        )
                    if result.get("errorId", 0) != 0:
                        return _rejected(
                            "2captcha",
                            str(
                                result.get("errorDescription")
                                or result.get("errorCode")
                                or "failed"
                            ),
                        )
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
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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

    supported = frozenset(
        {CaptchaChallengeType.RECAPTCHA.value, CaptchaChallengeType.HCAPTCHA.value}
    )
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
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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
            CaptchaChallengeType.SMARTCAPTCHA.value,
            CaptchaChallengeType.IMAGE.value,
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
        if wire_type == CaptchaChallengeType.IMAGE.value:
            try:
                body = await self._extract_image_with_wait(page)
                if not body:
                    return _rejected("capmonster", "captcha image could not be extracted")
                task: dict[str, Any] = {"type": "ImageToTextTask", "body": body}
                module = getattr(page, "_captcha_image_module", None)
                if isinstance(module, str) and module:
                    task["capMonsterModule"] = module
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.capmonster.cloud/createTask",
                        json={"clientKey": self.api_key, "task": task},
                    )
                    response.raise_for_status()
                    result = response.json()
                    task_id = result.get("taskId")
                    for _ in range(30):
                        if result.get("errorId", 0) != 0:
                            return _rejected(
                                "capmonster",
                                _provider_error_text(result, "image recognition failed"),
                            )
                        if result.get("status") == "ready":
                            solution = result.get("solution") or {}
                            text = solution.get("text") if isinstance(solution, dict) else None
                            return _token_result(
                                "capmonster",
                                text.strip() if isinstance(text, str) else "",
                                challenge_type=challenge_type,
                                task_id=str(task_id or ""),
                            )
                        if not task_id:
                            return _rejected("capmonster", "provider did not return a task id")
                        await sleep_with_source_deadline(1.0)
                        response = await client.post(
                            "https://api.capmonster.cloud/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": task_id},
                        )
                        response.raise_for_status()
                        result = response.json()
                return _timeout("capmonster")
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                return _unavailable("capmonster", exc)
        if wire_type == "cloudflare":
            return CaptchaSolveResult(
                solved=False,
                method="capmonster",
                error="cloudflare challenge requires browser-derived task parameters",
                failure_reason=CaptchaFailureReason.UNSUPPORTED_CHALLENGE,
                challenge_type=normalize_challenge_type(challenge_type),
                result_kind=CaptchaResultKind.UNSUPPORTED,
            )
        if wire_type == CaptchaChallengeType.SMARTCAPTCHA.value:
            return await self._solve_yandex_token(
                page, challenge_type=challenge_type, url=url, proxy_url=proxy_url
            )
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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
            action = await extract_recaptcha_action(page)
            if action:
                task_payload["pageAction"] = action
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

    async def _solve_yandex_token(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str,
    ) -> CaptchaSolveResult:
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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
        page_url = str(getattr(page, "url", "") or url or "")
        task_types = (
            ("YandexSmartCaptchaTask", "YandexCaptchaTask")
            if effective_proxy
            else ("YandexSmartCaptchaTaskProxyless", "YandexCaptchaTaskProxyless")
        )
        task_payload: dict[str, Any] = {
            "type": task_types[0],
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        user_agent = await _page_user_agent(page)
        if user_agent:
            task_payload["userAgent"] = user_agent
        proxy_fields = self._proxy_task_fields() if effective_proxy else {}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                created = None
                task_id = None
                reject_notes: list[str] = []
                for task_type in task_types:
                    task_payload["type"] = task_type
                    for key in (
                        "proxyType",
                        "proxyAddress",
                        "proxyPort",
                        "proxyLogin",
                        "proxyPassword",
                    ):
                        task_payload.pop(key, None)
                    if effective_proxy:
                        task_payload.update(proxy_fields)
                    created = (
                        await client.post(
                            "https://api.capmonster.cloud/createTask",
                            json={"clientKey": self.api_key, "task": task_payload},
                        )
                    ).json()
                    task_id = created.get("taskId")
                    if created.get("errorId", 0) == 0 and task_id:
                        break
                    reject_notes.append(
                        f"{task_type}:{_provider_error_text(created, 'rejected')}"
                    )
                    task_id = None
                if not task_id:
                    return _rejected("capmonster", ";".join(reject_notes) or "createTask failed")
                for _ in range(40):
                    await sleep_with_source_deadline(3.0)
                    result = (
                        await client.post(
                            "https://api.capmonster.cloud/getTaskResult",
                            json={"clientKey": self.api_key, "taskId": task_id},
                        )
                    ).json()
                    if result.get("status") == "ready":
                        solution = result.get("solution") or {}
                        return _token_result(
                            "capmonster",
                            solution.get("token") or solution.get("gRecaptchaResponse") or "",
                            challenge_type=challenge_type,
                            task_id=str(task_id),
                        )
                    if result.get("errorId", 0) != 0:
                        return _rejected(
                            "capmonster",
                            str(
                                result.get("errorDescription")
                                or result.get("errorCode")
                                or "failed"
                            ),
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
            CaptchaChallengeType.TURNSTILE.value,
        }
    )
    capability = CaptchaProviderCapability(
        provider="nextcaptcha",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        benchmark_candidate=True,
        notes="Cheap benchmark candidate for reCAPTCHA and Turnstile.",
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
        site_key = await self._extract_sitekey_with_wait(page, challenge_type)
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
        task_type = {
            (CaptchaChallengeType.RECAPTCHA.value, False): "RecaptchaV2TaskProxyless",
            (CaptchaChallengeType.RECAPTCHA.value, True): "RecaptchaV2Task",
            (CaptchaChallengeType.RECAPTCHA_V3.value, False): "RecaptchaV3TaskProxyless",
            (CaptchaChallengeType.RECAPTCHA_V3.value, True): "RecaptchaV3Task",
            (CaptchaChallengeType.TURNSTILE.value, False): "TurnstileTaskProxyless",
            (CaptchaChallengeType.TURNSTILE.value, True): "TurnstileTask",
        }[(wire_type, bool(effective_proxy))]
        task_payload: dict[str, Any] = {
            "type": task_type,
            "websiteURL": url or str(getattr(page, "url", "")),
            "websiteKey": site_key,
        }
        if wire_type == CaptchaChallengeType.RECAPTCHA_V3.value:
            action = await extract_recaptcha_action(page)
            if action:
                task_payload["pageAction"] = action
            task_payload["minScore"] = 0.3
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
                            solution.get("gRecaptchaResponse") or solution.get("token"),
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


@register_captcha_provider("cliproxy_image")
class CliproxyImageProvider(_BaseProvider):
    """Image-OCR fallback through an OpenAI-compatible CLIProxy vision model."""

    supported = frozenset({CaptchaChallengeType.IMAGE.value})
    capability = CaptchaProviderCapability(
        provider="cliproxy_image",
        supported_challenge_types=supported,
        result_kinds=frozenset({CaptchaResultKind.TOKEN}),
        free_or_dev=True,
        notes="Image OCR fallback after CapSolver; not a widget/token solver.",
    )

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: str = "",
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(api_key, proxy_url=proxy_url)
        if base_url is None or model is None:
            from job_ftch.config import get_settings

            settings = get_settings()
            if base_url is None:
                base_url = settings.captcha_vision_base_url
            if model is None:
                model = settings.captcha_vision_model
        self._base_url = str(base_url or "").strip().rstrip("/")
        self._model = str(model or "").strip()

    async def solve(
        self,
        page: Any,
        *,
        challenge_type: str,
        url: str,
        proxy_url: str = "",
    ) -> CaptchaSolveResult:
        del url, proxy_url
        if unsupported := self.unsupported(challenge_type, "cliproxy_image"):
            return unsupported
        if not self.api_key or not self._base_url or not self._model:
            return CaptchaSolveResult(
                solved=False,
                method="cliproxy_image",
                error="captcha vision base_url, model, or api_key is not configured",
                failure_reason=CaptchaFailureReason.MISSING_CREDENTIAL,
                challenge_type=CaptchaChallengeType.IMAGE.value,
                result_kind=CaptchaResultKind.TOKEN,
            )
        try:
            body = await self._extract_image_with_wait(page)
            if not isinstance(body, str) or not body:
                return _rejected("cliproxy_image", "captcha image could not be extracted")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    _chat_completions_url(self._base_url),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self._model,
                        "temperature": 0,
                        "max_tokens": 32,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Read the captcha. Reply with only the "
                                            "characters shown, no explanation."
                                        ),
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/png;base64,{body}"},
                                    },
                                ],
                            }
                        ],
                    },
                )
                response.raise_for_status()
                payload = response.json()
            text = _ocr_text_from_completion(payload)
            return _token_result(
                "cliproxy_image",
                text,
                challenge_type=challenge_type,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return _unavailable("cliproxy_image", exc)


def _chat_completions_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    return f"{trimmed}/v1/chat/completions"


def parse_vision_click_fractions(text: str) -> list[tuple[float, float]]:
    """Parse viewport-relative click fractions from a vision model reply."""
    raw = (text or "").strip()
    if not raw:
        return []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    clicks = data.get("clicks") if isinstance(data, dict) else data
    if not isinstance(clicks, list):
        return []
    points: list[tuple[float, float]] = []
    for item in clicks:
        if isinstance(item, dict):
            try:
                x = float(item["x"])
                y = float(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                x = float(item[0])
                y = float(item[1])
            except (TypeError, ValueError):
                continue
        else:
            continue
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            points.append((x, y))
    return points


async def request_vision_click_order(png: bytes) -> list[tuple[float, float]]:
    """Ask the configured vision model for SmartCaptcha click order."""
    import os

    from job_ftch.config import get_settings

    settings = get_settings()
    secret = getattr(settings, "openai_api_key", None)
    api_key = ""
    if secret is not None:
        api_key = str(secret.get_secret_value() or "").strip()
    if not api_key:
        api_key = (
            os.environ.get("JOB_FTCH_OPENAI_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
    base_url = str(settings.captcha_vision_base_url or "").strip()
    model = str(settings.captcha_vision_model or "").strip()
    if not png or not api_key or not base_url or not model:
        return []
    encoded = base64.b64encode(png).decode("ascii")
    prompt = (
        "This screenshot is a Yandex SmartCaptcha. A modal shows a painting with "
        "overlaid icons. Next to 'Press in the following order' is the required "
        "sequence. Click those overlay icons ON THE PAINTING in that order. Do not "
        "click the small instruction icons. Return JSON only: "
        '{"clicks":[{"x":0.12,"y":0.34}]} where x,y are fractions of this '
        "screenshot (0-1), origin top-left."
    )
    payload_json = {
        "model": model,
        "temperature": 0,
        "max_tokens": 256,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                _chat_completions_url(base_url),
                headers=headers,
                json=payload_json,
            )
            local_vision = "127.0.0.1" in base_url or "localhost" in base_url
            if response.status_code == 401 and local_vision:
                logger.info("smartcaptcha_vision_fallback_openai")
                payload_json = dict(payload_json)
                payload_json["model"] = "gpt-4.1-mini"
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload_json,
                )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.info(
            "smartcaptcha_vision_http_error",
            status=exc.response.status_code if exc.response is not None else None,
        )
        return []
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("smartcaptcha_vision_http_error", error=type(exc).__name__)
        return []
    text = ""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                item if isinstance(item, str) else str(item.get("text") or "")
                for item in content
                if isinstance(item, (str, dict))
            )
    return parse_vision_click_fractions(text)


def _ocr_text_from_completion(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        raw = "".join(parts)
    else:
        return ""
    first_line = raw.strip().strip("\"'").splitlines()[0].strip() if raw.strip() else ""
    return first_line if first_line and len(first_line) <= 64 else ""


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


def _session_result(
    method: str,
    cookies: dict[str, str],
    *,
    challenge_type: str = CaptchaChallengeType.UNKNOWN.value,
    task_id: str = "",
) -> CaptchaSolveResult:
    if not cookies:
        return CaptchaSolveResult(
            solved=False,
            method=method,
            error="provider returned no clearance cookies",
            failure_reason=CaptchaFailureReason.BAD_TOKEN,
            challenge_type=normalize_challenge_type(challenge_type),
            result_kind=CaptchaResultKind.SESSION,
            provider_task_id=task_id,
        )
    return CaptchaSolveResult(
        solved=True,
        method=method,
        cookies=cookies,
        challenge_type=normalize_challenge_type(challenge_type),
        result_kind=CaptchaResultKind.SESSION,
        provider_task_id=task_id,
    )


def _extract_solution_cookies(solution: Any) -> dict[str, str]:
    if not isinstance(solution, dict):
        return {}
    cookies: dict[str, str] = {}
    direct = solution.get("cf_clearance") or solution.get("clearance")
    if direct:
        cookies["cf_clearance"] = str(direct)

    raw_cookies = solution.get("cookies") or solution.get("cookie")
    if isinstance(raw_cookies, dict):
        for key, value in raw_cookies.items():
            if _is_clearance_cookie_name(str(key)):
                cookies[str(key)] = str(value)
    elif isinstance(raw_cookies, list):
        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "")
            value = item.get("value")
            if name and value is not None and _is_clearance_cookie_name(name):
                cookies[name] = str(value)
    elif isinstance(raw_cookies, str):
        for part in raw_cookies.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and _is_clearance_cookie_name(name):
                cookies[name.strip()] = value.strip()
    return cookies


def _is_clearance_cookie_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in {"cf_clearance", "cf_bm", "__cf_bm"}


def _provider_error_text(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    code = str(payload.get("errorCode") or "").strip()
    description = str(payload.get("errorDescription") or payload.get("error") or "").strip()
    if code and description:
        return f"{code}: {description}"
    return description or code or fallback


def _resolve_proxy_host_for_provider(hostname: str) -> str:
    try:
        candidates = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return hostname
    for family, _socktype, _proto, _canonname, sockaddr in candidates:
        if family is socket.AF_INET and sockaddr:
            return str(sockaddr[0])
    for _family, _socktype, _proto, _canonname, sockaddr in candidates:
        if sockaddr:
            return str(sockaddr[0])
    return hostname


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
