from typing import Any

from application.registry import register_bypass


class ProxyRotatorBypass:
    """Rotate HTTP proxies on each request. Requires proxy_list in bypass_config."""

    def __init__(self, proxy_list: list[str]) -> None:
        self._proxies = proxy_list
        self._index = 0

    def configure(self, client: Any) -> Any:
        if not self._proxies:
            return client
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        # Return a new httpx.AsyncClient with proxy set
        import httpx

        if isinstance(client, httpx.AsyncClient):
            return httpx.AsyncClient(proxy=proxy, headers=client.headers)
        return client


@register_bypass("proxy_rotator")
def _create_proxy_rotator(
    bypass_config: dict[str, str] | None = None,
) -> ProxyRotatorBypass:
    config = bypass_config or {}
    proxy_list_raw = config.get("proxy_list", "")
    proxies = [p.strip() for p in proxy_list_raw.split(",") if p.strip()]
    return ProxyRotatorBypass(proxies)
