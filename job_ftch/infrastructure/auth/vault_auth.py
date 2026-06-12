"""HashiCorp Vault / AWS Secrets Manager auth provider stub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.registry import register_auth_provider

if TYPE_CHECKING:
    from job_ftch.config import Settings


class VaultAuthProvider:
    """HashiCorp Vault / AWS Secrets Manager auth provider stub.

    Install: pip install hvac (HashiCorp Vault) or boto3 (AWS)
    """

    def __init__(self, vault_addr: str | None = None) -> None:
        raise NotImplementedError(
            "VaultAuthProvider is not yet implemented. "
            "Install hvac and implement infrastructure/auth/vault_auth.py. "
            "See docs/auth/vault.md for the implementation contract."
        )

    def resolve(self, source_id: str) -> dict[str, str]:
        raise NotImplementedError


@register_auth_provider("vault")
def _create_vault_auth(settings: Settings) -> VaultAuthProvider:
    del settings
    msg = "VaultAuthProvider is not implemented in this project."
    raise NotImplementedError(msg)
