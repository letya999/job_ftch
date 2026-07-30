from typing import Any

from job_ftch.application.registry import register_bypass


class ManagedScraperBypass:
    """Delegates HTTP fetch to a managed scraping API.

    Supported providers: "scrapfly", "zenrows", "browserless"
    Requires API key in bypass_config["api_key"] (resolved via AuthProvider).

    This is the recommended production path for CloudFlare-protected sites.
    """

    def __init__(self, api_url: str, api_key: str, provider: str = "scrapfly") -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._provider = provider

    async def apply_http(self, client: Any) -> Any:
        """Returns a new httpx.AsyncClient configured to route via the managed API."""
        import httpx

        from job_ftch.infrastructure.network.ssrf_guard import SSRFGuardedTransport

        transport = SSRFGuardedTransport(httpx.AsyncHTTPTransport())

        from job_ftch.infrastructure.network.ssrf_guard import SSRFGuardedTransport

        transport = SSRFGuardedTransport(httpx.AsyncHTTPTransport())
        timeout_seconds = getattr(client, "timeout", None)
        base_headers = getattr(client, "headers", {})

        class ManagedHttpxAdapter:
            def __init__(self, provider: str, api_url: str, api_key: str) -> None:
                self._provider = provider
                self._api_url = api_url
                self._api_key = api_key
                self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
                self._base_headers = getattr(base_headers, "copy", lambda: dict(base_headers))()

            @property
            def headers(self) -> httpx.Headers:
                h = dict(self._base_headers)
                if self._provider == "scrapfly":
                    h["scp-sdk"] = "python"
                return httpx.Headers(h)

            @property
            def base_url(self) -> str:
                return self._api_url

            @property
            def params(self) -> dict[str, Any]:
                if self._provider == "scrapfly":
                    return {"key": self._api_key}
                elif self._provider == "zenrows":
                    return {"apikey": self._api_key}
                elif self._provider == "browserless":
                    return {"token": self._api_key}
                return {}

            async def __aenter__(self) -> "ManagedHttpxAdapter":
                return self

            async def __aexit__(self, *args: object, **kwargs: object) -> None:
                await self._client.aclose()

            def _build_request_args(
                self, target_url: str, **kwargs: Any
            ) -> tuple[str, dict[str, Any]]:
                req_kwargs = dict(kwargs)
                if self._provider == "scrapfly":
                    req_kwargs.setdefault("headers", {}).update(self.headers)
                    params = req_kwargs.pop("params", {}) or {}
                    params["key"] = self._api_key
                    params["url"] = target_url
                    req_kwargs["params"] = params
                    return self._api_url, req_kwargs
                if self._provider in ("zenrows", "browserless"):
                    req_kwargs.setdefault("headers", {}).update(self.headers)
                    params = req_kwargs.pop("params", {}) or {}
                    params.update(self.params)
                    params["url"] = target_url
                    req_kwargs["params"] = params
                    return self._api_url, req_kwargs
                return target_url, req_kwargs

            async def get(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                provider_url, req_kwargs = self._build_request_args(url, **kwargs)
                return await self._client.get(
                    provider_url, follow_redirects=follow_redirects, **req_kwargs
                )

            async def post(self, url: str, follow_redirects: bool = False, **kwargs: Any) -> Any:
                provider_url, req_kwargs = self._build_request_args(url, **kwargs)
                return await self._client.post(
                    provider_url, follow_redirects=follow_redirects, **req_kwargs
                )

        return ManagedHttpxAdapter(self._provider, self._api_url, self._api_key)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


@register_bypass("managed_scraper")
def _create_managed(bypass_config: dict[str, Any] | None = None) -> ManagedScraperBypass:
    config = bypass_config or {}
    return ManagedScraperBypass(
        api_url=config.get("api_url", ""),
        api_key=config.get("api_key", ""),
        provider=config.get("provider", "scrapfly"),
    )
