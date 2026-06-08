from typing import Any

from application.registry import register_bypass


class CaptchaSolverBypass:
    """Integrates Capsolver or 2captcha for CAPTCHA challenges.

    Community-maintained. Requires API key in bypass_config["api_key"].
    Providers: "capsolver", "2captcha"
    """

    def __init__(self, provider: str, api_key: str) -> None:
        self._provider = provider
        self._api_key = api_key

    def configure(self, client: Any) -> Any:
        raise NotImplementedError(
            f"CaptchaSolverBypass (provider={self._provider!r}) is not implemented. "
            "Implement in infrastructure/bypass/captcha_solver.py."
        )


@register_bypass("captcha_solver")
def _create_captcha_solver(
    bypass_config: dict[str, str] | None = None,
) -> CaptchaSolverBypass:
    config = bypass_config or {}
    return CaptchaSolverBypass(
        provider=config.get("provider", "capsolver"),
        api_key=config.get("api_key", ""),
    )
