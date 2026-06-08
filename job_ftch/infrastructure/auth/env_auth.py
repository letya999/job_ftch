"""Environment-variable-based credential resolution."""

from __future__ import annotations

import os


class EnvAuthProvider:
    """Reads JOB_FTCH_AUTH_{SOURCE_ID}_{KEY} env vars."""

    def resolve(self, source_id: str) -> dict[str, str]:
        prefix = f"JOB_FTCH_AUTH_{source_id.upper().replace('-', '_')}_"
        return {
            key[len(prefix) :].lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }
