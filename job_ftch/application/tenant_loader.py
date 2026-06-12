"""Load tenant configurations from a directory or aggregate file."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from job_ftch.domain import TenantConfig

if TYPE_CHECKING:
    from pathlib import Path


def _load_file(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            msg = "PyYAML is required to load tenant YAML config files."
            raise RuntimeError(msg) from exc
        return yaml.safe_load(text)
    return json.loads(text)


def load_tenants(configs_dir: Path) -> list[TenantConfig]:
    if not configs_dir.exists():
        msg = f"configs_dir does not exist: {configs_dir}"
        raise FileNotFoundError(msg)
    if not configs_dir.is_dir():
        msg = f"configs_dir is not a directory: {configs_dir}"
        raise NotADirectoryError(msg)

    tenants: list[TenantConfig] = []
    for path in sorted(configs_dir.iterdir()):
        if path.suffix not in {".yaml", ".yml", ".json"} or not path.is_file():
            continue
        payload = _load_file(path)
        if isinstance(payload, dict) and isinstance(payload.get("tenants"), list):
            tenants.extend(TenantConfig.model_validate(item) for item in payload["tenants"])
            continue
        tenants.append(TenantConfig.model_validate(payload))

    seen: set[str] = set()
    for tenant in tenants:
        if tenant.tenant_id in seen:
            msg = f"Duplicate tenant_id detected: {tenant.tenant_id}"
            raise ValueError(msg)
        seen.add(tenant.tenant_id)
    return tenants
