import builtins
from typing import Any, cast

from job_ftch.application.registry import BypassCapability, register_bypass
from job_ftch.config import get_settings


def _load_curl_session() -> tuple[Any | None, BaseException | None]:
    """Import curl-cffi, applying its temporary Python 3.12 compatibility fix.

    curl-cffi 0.15.0 fixes PYSEC-2026-2431 but omitted ``typing.Any`` from
    one module.  The failed import is retried with that annotation name
    temporarily available through builtins; no project-wide symbol remains.
    """
    try:
        from curl_cffi.requests import AsyncSession

        return AsyncSession, None
    except NameError as exc:
        if "Any" not in str(exc):
            return None, exc
        had_any = hasattr(builtins, "Any")
        previous = getattr(builtins, "Any", None)
        builtins.__dict__["Any"] = Any
        try:
            from curl_cffi.requests import AsyncSession

            return AsyncSession, None
        except BaseException as retry_exc:  # pragma: no cover - dependency-specific fallback
            return None, retry_exc
        finally:
            if had_any:
                builtins.__dict__["Any"] = previous
            else:
                delattr(builtins, "Any")
    except ImportError as exc:  # pragma: no cover
        return None, exc


_CurlSession, _IMPORT_ERROR = _load_curl_session()
_CURL_AVAILABLE = _CurlSession is not None

_CHROME_IMPERSONATION_POOL: tuple[str, ...] = (
    "chrome",
    "chrome131",
    "chrome124",
)

_NON_CHROME_IMPERSONATION: dict[str, str] = {
    "safari": "safari17_5",
    "firefox": "firefox125",
}

_CHROME_H2_SETTINGS: dict[str, Any] = {
    "SETTINGS_MAX_CONCURRENT_STREAMS": 1000,
    "SETTINGS_INITIAL_WINDOW_SIZE": 6291456,
    "SETTINGS_MAX_HEADER_LIST_SIZE": 262144,
    "SETTINGS_ENABLE_PUSH": 1,
}


def _select_impersonate(url: str, default: str) -> str:
    """Select impersonation target based on domain and FingerprintProfile.

    If default is not 'chrome' (auto), FingerprintProfile already set it explicitly.
    Otherwise rotate per-domain via a stable SHA-256 hash for sticky selection.
    """
    if default != "chrome":
        return default

    import hashlib
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.lower()
    if not domain:
        return default

    hash_val = int(hashlib.sha256(domain.encode()).hexdigest(), 16)
    idx = hash_val % len(_CHROME_IMPERSONATION_POOL)
    return _CHROME_IMPERSONATION_POOL[idx]


class CurlBypass:
    """Uses curl_cffi to spoof TLS fingerprints (e.g. Chrome) on HTTP requests.
    Bypasses Cloudflare on pure HTTP fetch without needing a browser.

    Supports impersonation rotation: when no explicit impersonate is configured,
    selects a sticky per-domain target from a pool of browser fingerprints
    (chrome131, chrome124, safari17_5, firefox125, etc.).

    HTTP/2 fingerprint is controlled via curl_cffi's impersonation profiles,
    which already include correct SETTINGS frame order, WINDOW_UPDATE, and
    priority frames matching real Chrome/Firefox/Safari behavior.
    """

    def __init__(self, impersonate: str = "chrome") -> None:
        self.impersonate = impersonate
        self._proxy_url: str | None = None

    def set_proxy_url(self, proxy_url: str | None) -> None:
        """Bind an explicit route selected by the adaptive controller."""
        self._proxy_url = proxy_url

    async def apply_http(self, client: Any) -> Any:
        if _CurlSession is None:
            raise ImportError(
                "Curl bypass requires the 'stealth' extra: pip install job-ftch[stealth]"
            ) from _IMPORT_ERROR
        session_factory = cast("Any", _CurlSession)

        timeout_seconds = _resolve_timeout_seconds(client)

        import os

        proxy_url = self._proxy_url or os.environ.get("JOB_FTCH_HTTP_PROXY")

        class CurlHttpxAdapter:
            def __init__(self, bypass: "CurlBypass", timeout: float) -> None:
                self._bypass = bypass
                self._timeout = timeout
                self._sessions: dict[str, Any] = {}

            def _get_session(self, url: str) -> Any:
                impersonate = _select_impersonate(url, self._bypass.impersonate)
                sess = self._sessions.get(impersonate)
                if sess is None:
                    kwargs: dict[str, Any] = {
                        "impersonate": cast("Any", impersonate),
                        "timeout": self._timeout,
                    }
                    if proxy_url:
                        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
                    sess = session_factory(**kwargs)
                    self._sessions[impersonate] = sess
                return sess

            async def __aenter__(self) -> "CurlHttpxAdapter":
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                for sess in self._sessions.values():
                    await sess.close()
                self._sessions.clear()

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                from job_ftch.infrastructure.sources.ssrf_guard import check_ssrf

                await check_ssrf(url)
                kwargs.setdefault("timeout", self._timeout)
                sess = self._get_session(url)
                return await sess.get(url, allow_redirects=follow_redirects, **kwargs)

            async def post(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                from job_ftch.infrastructure.sources.ssrf_guard import check_ssrf

                await check_ssrf(url)
                kwargs.setdefault("timeout", self._timeout)
                sess = self._get_session(url)
                return await sess.post(url, allow_redirects=follow_redirects, **kwargs)

        return CurlHttpxAdapter(self, timeout_seconds)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


def _create_curl(bypass_config: dict[str, Any] | None = None) -> CurlBypass:
    config = bypass_config or {}
    bypass = CurlBypass(impersonate=str(config.get("impersonate", "chrome")))
    try:
        from job_ftch.infrastructure.bypass.fingerprint_profile import patch_curl_bypass

        patch_curl_bypass(bypass)
    except ImportError:
        pass
    return bypass


if _CURL_AVAILABLE:
    register_bypass(
        "curl_stealth",
        capability=BypassCapability(
            cost=10,
            transport="curl_impersonation",
            challenge_actions=frozenset({"tls_impersonation"}),
        ),
    )(_create_curl)


def _resolve_timeout_seconds(client: Any) -> float:
    timeout = getattr(client, "timeout", None)
    if timeout is not None:
        for attr in ("read", "connect", "write", "pool"):
            value = getattr(timeout, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        value = getattr(timeout, "timeout", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    settings = get_settings()
    return max(settings.monitor_timeout_seconds, 0.1)
