"""File-based credential resolution (secrets.yaml)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from job_ftch.application.registry import register_auth_provider

if TYPE_CHECKING:
    from pathlib import Path

    from job_ftch.config import Settings


class FileAuthProvider:
    """Reads secrets from a YAML/JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._secrets: dict[str, dict[str, str]] = {}
        self._loaded = False

    def _load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                self._secrets = data
        self._loaded = True

    def resolve(self, source_id: str) -> dict[str, str]:
        if not self._loaded:
            self._load()
        return self._secrets.get(source_id, {})


@register_auth_provider("file")
def _create_file_auth(settings: Settings) -> FileAuthProvider:
    if settings.auth_file_path is None:
        msg = "auth_file_path is required when auth_provider=file."
        raise ValueError(msg)
    return FileAuthProvider(settings.auth_file_path)
