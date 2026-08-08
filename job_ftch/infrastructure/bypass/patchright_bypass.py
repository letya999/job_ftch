from __future__ import annotations

from typing import Any

from job_ftch.application.registry import BypassCapability, register_bypass

try:
    import patchright.async_api  # noqa: F401

    _PATCHRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _PATCHRIGHT_AVAILABLE = False


class PatchrightBrowserBypass:
    """Explicit Patchright browser tier.

    Patchright is already the preferred browser backend in ``browser_utils``.
    This strategy makes that capability route-visible and adds the launch flags
    that belong to the Patchright-specific escalation step.
    """

    requires_process_identity = True

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        args = list(kwargs.get("args", []) or [])
        process_user_agent = kwargs.pop("_process_identity_user_agent", None)
        process_locale = kwargs.pop("_process_identity_locale", None)
        for arg in (
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
        ):
            if arg not in args:
                args.append(arg)
        if process_user_agent and not any(arg.startswith("--user-agent=") for arg in args):
            args.append(f"--user-agent={process_user_agent}")
        if process_locale and not any(arg.startswith("--lang=") for arg in args):
            args.append(f"--lang={process_locale}")
        kwargs["args"] = args
        kwargs["_patchright_required"] = True
        return kwargs

    async def apply_page(self, page: Any) -> None:
        del page


def _create_patchright(bypass_config: dict[str, Any] | None = None) -> PatchrightBrowserBypass:
    del bypass_config
    return PatchrightBrowserBypass()


if _PATCHRIGHT_AVAILABLE:
    register_bypass(
        "patchright_browser",
        capability=BypassCapability(
            cost=25,
            browser_family="chromium_patchright",
            challenge_actions=frozenset({"fingerprint_resistant", "generic_challenge"}),
            min_remaining_seconds=8.0,
        ),
    )(_create_patchright)
