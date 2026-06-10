from typing import Any

from job_ftch.application.registry import register_bypass


class NoopBypass:
    """Default bypass: no modification to the HTTP client or browser."""

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        pass


@register_bypass("noop")
def _create_noop() -> NoopBypass:
    return NoopBypass()
