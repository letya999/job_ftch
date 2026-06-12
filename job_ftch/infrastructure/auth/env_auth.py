"""Environment-variable-based credential resolution."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from job_ftch.application.registry import register_auth_provider

if TYPE_CHECKING:
    from job_ftch.config import Settings


class EnvAuthProvider:
    """Reads JOB_FTCH_AUTH_{SOURCE_ID}_{KEY} env vars."""

    def resolve(self, source_id: str) -> dict[str, str]:
        prefix = f"JOB_FTCH_AUTH_{source_id.upper().replace('-', '_')}_"
        return {
            key[len(prefix) :].lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }


@register_auth_provider("env")
def _create_env_auth(settings: Settings) -> EnvAuthProvider:
    del settings
    return EnvAuthProvider()
