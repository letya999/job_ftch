"""Auth provider resolution for runtime adapters and tenant runners."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
from job_ftch.infrastructure.auth.file_auth import FileAuthProvider

if TYPE_CHECKING:
    from job_ftch.config import Settings


def resolve_auth_provider(
    provider_name: str | None,
    *,
    settings: Settings,
) -> object:
    normalized = (provider_name or "env").strip().lower()
    if normalized == "env":
        return EnvAuthProvider()
    if normalized == "file":
        if settings.auth_file_path is None:
            msg = "auth_file_path is required when auth_provider=file."
            raise ValueError(msg)
        return FileAuthProvider(settings.auth_file_path)
    if normalized == "vault":
        msg = "VaultAuthProvider is not implemented in this project."
        raise NotImplementedError(msg)
    msg = f"Unsupported auth provider: {provider_name}"
    raise ValueError(msg)
