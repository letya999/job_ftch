from typing import Any, cast

from job_ftch.application.registry import register_bypass

try:
    from curl_cffi import requests
    _IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover
    requests = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

# impersonate value passed as str; curl_cffi validates at runtime


class CurlBypass:
    """Uses curl_cffi to spoof TLS fingerprints (e.g. Chrome) on HTTP requests.
    Bypasses Cloudflare on pure HTTP fetch without needing a browser.
    """

    def __init__(self, impersonate: str = "chrome120") -> None:
        self.impersonate = impersonate

    async def apply_http(self, client: Any) -> Any:
        if requests is None:
            raise ImportError(
                "Curl bypass requires the 'stealth' extra: pip install job-ftch[stealth]"
            ) from _IMPORT_ERROR

        # Create an AsyncSession from curl_cffi that has the same basic async interface as httpx
        # cast(Any) avoids the Literal mismatch — curl_cffi validates the value at runtime
        session: Any = requests.AsyncSession(impersonate=cast("Any", self.impersonate))

        # We need a wrapper to make curl_cffi interface strictly match httpx for our usage
        class CurlHttpxAdapter:
            def __init__(self, sess: Any) -> None:
                self._sess = sess

            async def __aenter__(self) -> "CurlHttpxAdapter":
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                await self._sess.close()

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                resp = await self._sess.get(url, allow_redirects=follow_redirects, **kwargs)
                # resp.text is a property in curl_cffi, same as httpx
                # resp.status_code is also a property in curl_cffi
                return resp

        return CurlHttpxAdapter(session)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


@register_bypass("curl_stealth")
def _create_curl(bypass_config: dict[str, str] | None = None) -> CurlBypass:
    config = bypass_config or {}
    return CurlBypass(impersonate=config.get("impersonate", "chrome120"))
