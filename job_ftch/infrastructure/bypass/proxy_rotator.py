import os
from typing import Any

from job_ftch.application.registry import register_bypass


class ProxyRotatorBypass:
    """Rotate HTTP proxies on each request. Requires proxy_list in bypass_config."""

    def __init__(self, proxy_list: list[str] | None = None) -> None:
        self._proxies = proxy_list or []
        self._index = 0

    def _get_next_proxy(self) -> str | None:
        if self._proxies:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy
        # Fallback to env
        return os.environ.get("JOB_FTCH_HTTP_PROXY")

    async def apply_http(self, client: Any) -> Any:
        proxy = self._get_next_proxy()
        if not proxy:
            return client

        import httpx

        if isinstance(client, httpx.AsyncClient):
            return httpx.AsyncClient(proxy=proxy, headers=client.headers, timeout=client.timeout)
        # If it's a curl_cffi client, we can configure it too, but let's assume it handles proxy via dict
        if hasattr(client, "proxies"):
            client.proxies = {"http": proxy, "https": proxy}
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        proxy = self._get_next_proxy()
        if proxy:
            from playwright.async_api import ProxySettings

            kwargs["proxy"] = ProxySettings(server=proxy)
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


@register_bypass("proxy_rotator")
def _create_proxy_rotator(bypass_config: dict[str, str] | None = None) -> ProxyRotatorBypass:
    config = bypass_config or {}
    proxy_list_raw = config.get("proxy_list", "")
    proxies = [p.strip() for p in proxy_list_raw.split(",") if p.strip()]
    return ProxyRotatorBypass(proxies)
