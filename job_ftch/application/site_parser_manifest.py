"""YAML-backed manifest for site parser registration and runtime defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from job_ftch.config import resolve_site_parsers_manifest_path


class SiteParserManifestBrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scroll_loops: int | None = None
    scroll_pause_ms: int | None = None
    scroll_px: int | None = None
    stale_rounds: int | None = None
    headless: bool | None = None
    stealth: bool | None = None
    wait: str | None = None


class SiteParserManifestRuntimeDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url_filter: Any | None = None
    render: bool | None = None
    wait: str | None = None
    include_if_detail_page: bool | None = None
    expand_links: tuple[str, ...] | None = None
    extra: dict[str, Any] | None = None


class SiteParserManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    domain_pattern: str
    has_custom_parse: bool
    supports_discover: bool = False
    parser_kind: str | None = None
    runtime_defaults: SiteParserManifestRuntimeDefaults | None = None
    browser: SiteParserManifestBrowserConfig | None = None
    api_path: str | None = None
    api_url: str | None = None
    headers: dict[str, str] | None = None
    selectors: dict[str, str] | None = None
    url_filter: Any | None = None
    render: bool | None = None
    wait: str | None = None
    include_if_detail_page: bool | None = None
    expand_links: tuple[str, ...] | None = None
    detail_pattern: str | None = None
    listing_pattern: str | None = None
    limit: int | None = None
    timeout_ms: int | None = None
    detail_timeout_ms: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def merged_runtime_defaults(self) -> dict[str, Any]:
        payload = (
            self.runtime_defaults.model_dump(exclude_none=True)
            if self.runtime_defaults is not None
            else {}
        )
        if self.url_filter is not None:
            payload["url_filter"] = self.url_filter
        if self.render is not None:
            payload["render"] = self.render
        if self.wait is not None:
            payload["wait"] = self.wait
        if self.include_if_detail_page is not None:
            payload["include_if_detail_page"] = self.include_if_detail_page
        if self.expand_links is not None:
            payload["expand_links"] = self.expand_links
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload


class SiteParserManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parsers: list[SiteParserManifestEntry]


_MANIFEST_CACHE: dict[object, SiteParserManifest] = {}


def clear_site_parser_manifest_cache() -> None:
    _MANIFEST_CACHE.clear()


def load_site_parser_manifest(path: Path | None = None) -> SiteParserManifest:
    resolved_path = resolve_site_parsers_manifest_path(path)
    cached = _MANIFEST_CACHE.get(resolved_path)
    if cached is not None:
        return cached

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        msg = "PyYAML is required to load site parser manifests."
        raise RuntimeError(msg) from exc

    try:
        raw = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise
    except Exception as exc:
        msg = f"Failed to read site parser manifest at {resolved_path}: {exc}"
        raise ValueError(msg) from exc

    try:
        manifest = SiteParserManifest.model_validate(raw)
    except ValidationError as exc:
        msg = f"Invalid site parser manifest at {resolved_path}: {exc}"
        raise ValueError(msg) from exc

    _MANIFEST_CACHE[resolved_path] = manifest
    return manifest


def get_site_parser_manifest_entry(
    name: str,
    path: Path | None = None,
) -> SiteParserManifestEntry | None:
    manifest = load_site_parser_manifest(path)
    for entry in manifest.parsers:
        if entry.name == name:
            return entry
    return None


def build_runtime_defaults_from_manifest(
    entry: SiteParserManifestEntry,
) -> dict[str, Any] | None:
    payload = entry.merged_runtime_defaults()
    if not payload:
        return None
    return payload
