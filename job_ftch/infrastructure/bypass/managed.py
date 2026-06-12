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

        base_url = self._api_url
        headers: dict[str, str] = {}
        if self._provider == "scrapfly":
            headers["scp-sdk"] = "python"
            return httpx.AsyncClient(
                base_url=base_url,
                headers={**getattr(client, "headers", {}), **headers},
                params={"key": self._api_key},
            )
        if self._provider in ("zenrows", "browserless"):
            return httpx.AsyncClient(
                base_url=base_url,
                headers={
                    **getattr(client, "headers", {}),
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


@register_bypass("managed_scraper")
def _create_managed(bypass_config: dict[str, str] | None = None) -> ManagedScraperBypass:
    config = bypass_config or {}
    return ManagedScraperBypass(
        api_url=config.get("api_url", ""),
        api_key=config.get("api_key", ""),
        provider=config.get("provider", "scrapfly"),
    )
