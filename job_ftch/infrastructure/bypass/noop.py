from typing import Any

from job_ftch.application.registry import register_bypass


class NoopBypass:
    """Default bypass: no modification to the HTTP client."""

    def configure(self, client: Any) -> Any:
        return client


@register_bypass("noop")
def _create_noop() -> NoopBypass:
    return NoopBypass()
