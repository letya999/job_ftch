from typing import Any

from job_ftch.application.registry import register_bypass

try:
    import cloakbrowser

    _CLOAK_AVAILABLE = True
except ImportError:
    _CLOAK_AVAILABLE = False


class CloakBrowserBypass:
    """Uses a custom compiled CloakBrowser binary for ultimate stealth.
    Automatically downloads and uses the CloakBrowser chromium binary.
    """

    def __init__(self, executable_path: str | None = None) -> None:
        self._executable_path = executable_path

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # Only apply executable_path and stealth args to launch_kwargs, not context_kwargs
        if "user_agent" in kwargs or "viewport" in kwargs:
            return kwargs

        if not _CLOAK_AVAILABLE and not self._executable_path:
            import structlog

            structlog.get_logger().warning(
                "cloakbrowser not installed and no executable_path provided"
            )
            return kwargs

        exe_path = self._executable_path
        if not exe_path and _CLOAK_AVAILABLE:
            exe_path = cloakbrowser.ensure_binary()

        if exe_path:
            kwargs["executable_path"] = exe_path

        args = kwargs.get("args", [])

        if _CLOAK_AVAILABLE:
            stealth_args = cloakbrowser.get_default_stealth_args()
            for arg in stealth_args:
                if arg not in args:
                    args.append(arg)

        # CloakBrowser specific args
        if "--humanize=true" not in args:
            args.append("--humanize=true")

        kwargs["args"] = args
        return kwargs

    async def apply_page(self, page: Any) -> None:
        # CloakBrowser handles stealth natively in C++, no JS injections needed.
        pass


@register_bypass("cloak")
def _create_cloak(bypass_config: dict[str, str] | None = None) -> CloakBrowserBypass:
    config = bypass_config or {}
    import os

    exe_path = config.get("executable_path") or os.environ.get("CLOAK_BROWSER_PATH")
    return CloakBrowserBypass(executable_path=exe_path)
