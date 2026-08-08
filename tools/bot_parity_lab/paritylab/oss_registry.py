from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ADMISSIBLE_STATUSES = {"adapter-ready", "adopted"}


class OSSRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OSSComponent:
    component_id: str
    name: str
    version: str
    license: str
    source: str
    mode: str
    status: str
    namespace: str
    asset_path: str | None
    asset_sha256: str | None
    surfaces: tuple[str, ...]

    @property
    def accepts_evidence(self) -> bool:
        return self.status in ADMISSIBLE_STATUSES


class OSSRegistry:
    def __init__(self, components: tuple[OSSComponent, ...], source_path: Path) -> None:
        self.components = components
        self.source_path = source_path
        self._by_id = {item.component_id: item for item in components}

    def require_evidence_adapter(self, component_id: str) -> OSSComponent:
        component = self._by_id.get(component_id)
        if component is None or not component.accepts_evidence:
            raise OSSRegistryError("unknown or non-admitted OSS evidence adapter")
        return component

    def audit(self) -> list[str]:
        errors: list[str] = []
        if len(self._by_id) != len(self.components):
            errors.append("component ids must be unique")
        namespaces = [item.namespace for item in self.components]
        if len(set(namespaces)) != len(namespaces):
            errors.append("component namespaces must be unique")
        for item in self.components:
            prefix = item.component_id
            if not _ID_RE.fullmatch(item.component_id):
                errors.append(f"{prefix}: invalid id")
            if not item.license or item.license.lower() in {"unknown", "tbd"}:
                errors.append(f"{prefix}: license is not reviewed")
            if not item.source.startswith("https://github.com/"):
                errors.append(f"{prefix}: source must be an official HTTPS repository")
            if item.accepts_evidence and not _VERSION_RE.fullmatch(item.version):
                errors.append(f"{prefix}: admitted adapters require an exact semantic version")
            if item.asset_sha256 is not None and not _SHA256_RE.fullmatch(item.asset_sha256):
                errors.append(f"{prefix}: asset_sha256 must be lowercase SHA-256")
            if item.status == "adopted" and item.asset_sha256 is None:
                errors.append(f"{prefix}: adopted browser assets require a checksum")
            if item.status == "adopted" and item.asset_path is None:
                errors.append(f"{prefix}: adopted browser assets require a local path")
            if item.status == "adopted" and item.asset_path and item.asset_sha256:
                asset = self.source_path.parent.parent / item.asset_path
                if not asset.is_file():
                    errors.append(f"{prefix}: admitted local asset is missing")
                elif hashlib.sha256(asset.read_bytes()).hexdigest() != item.asset_sha256:
                    errors.append(f"{prefix}: admitted local asset checksum mismatch")
            if item.namespace != f"vendor:{item.component_id}":
                errors.append(f"{prefix}: namespace must match component id")
        return errors

    def verify_asset(self, component_id: str, asset: Path) -> bool:
        component = self.require_evidence_adapter(component_id)
        if component.asset_sha256 is None:
            raise OSSRegistryError("component has no admitted local asset checksum")
        return hashlib.sha256(asset.read_bytes()).hexdigest() == component.asset_sha256


def load_oss_registry(path: Path) -> OSSRegistry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("components"), list):
        raise OSSRegistryError("unsupported OSS registry schema")
    components = tuple(
        OSSComponent(
            component_id=str(item["id"]),
            name=str(item["name"]),
            version=str(item["version"]),
            license=str(item["license"]),
            source=str(item["source"]),
            mode=str(item["mode"]),
            status=str(item["status"]),
            namespace=str(item["namespace"]),
            asset_path=str(item["asset_path"]) if item.get("asset_path") else None,
            asset_sha256=str(item["asset_sha256"]) if item.get("asset_sha256") else None,
            surfaces=tuple(str(value) for value in item.get("surfaces", [])),
        )
        for item in raw["components"]
    )
    registry = OSSRegistry(components, path)
    errors = registry.audit()
    if errors:
        raise OSSRegistryError("; ".join(errors))
    return registry
