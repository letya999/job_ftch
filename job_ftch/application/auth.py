"""Auth provider resolution for runtime adapters and tenant runners."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.registry import create_auth_provider

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.config import Settings


def resolve_auth_provider(
    provider_name: str | None,
    *,
    settings: Settings,
) -> AuthProvider:
    return create_auth_provider(provider_name, settings)
