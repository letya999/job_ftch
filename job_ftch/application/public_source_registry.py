"""Build public-safe source registry views from runtime listings.

Source of truth is the same runtime catalog used by bot/API/MCP
(``TenantRunner.list_sources`` / tenant store overlay). Fixtures are never
consulted. Output is allowlist-only; secret-bearing and private fields are
dropped or redacted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from job_ftch.domain.public_source_registry import (
    PublicRegistryStatus,
    PublicSourceRegistry,
    PublicSourceRegistryEntry,
    PublicSourceStatus,
)

# Tenants whose source list may be published without authentication.
DEFAULT_PUBLIC_TENANT_ALLOWLIST: frozenset[str] = frozenset({"ai_jobs"})

# Source kinds that must never appear on a public registry (local-only paths).
_HIDDEN_KINDS: frozenset[str] = frozenset({"local_fixture"})

# Keys that must never appear on a public payload even if present in listings.
SENSITIVE_LISTING_KEYS: frozenset[str] = frozenset(
    {
        "spec",
        "assessment",
        "requirements",
        "added_by",
        "added_via",
        "input_value",
        "auth_source_id",
        "monitor_config",
        "scraper_config",
        "headers",
        "cookies",
        "token",
        "tokens",
        "proxy",
        "proxies",
        "password",
        "secret",
        "credentials",
        "api_key",
        "authorization",
        "browser_profile",
        "profile_path",
        "session",
        "cookie_jar",
        "user_id",
        "tenant_id",
    }
)

_PUBLIC_USERNAME_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{3,63}$")
_NUMERIC_ENTITY_RE = re.compile(r"^-?\d{5,}$")
_INVITE_MARKERS = ("joinchat", "+")
_SECRETISH_RE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization|cookie|proxy|"
    r"bearer\s+[a-z0-9._\-]{8,}|[a-f0-9]{32,})"
)
_PATH_RE = re.compile(r"(?i)([a-z]:\\|/)[^\s]{8,}")
_MAX_FAILURE_REASON_LEN = 160


def is_public_tenant(
    tenant_slug: str,
    *,
    allowlist: frozenset[str] | None = None,
) -> bool:
    """Return True when the tenant slug is allowed on the public registry."""
    resolved = allowlist if allowlist is not None else DEFAULT_PUBLIC_TENANT_ALLOWLIST
    return tenant_slug in resolved


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _is_public_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(parsed.netloc) and "@" not in parsed.netloc


def _is_public_telegram_handle(entity: str) -> bool:
    text = entity.strip()
    if not text:
        return False
    lowered = text.casefold()
    if "t.me/" in lowered or "telegram.me/" in lowered:
        # Public channel URLs like https://t.me/name are handled via URL path.
        path = urlsplit(text).path if "://" in text else text
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        if slug.startswith("+") or "joinchat" in path.casefold():
            return False
        return bool(_PUBLIC_USERNAME_RE.fullmatch(slug))
    if any(marker in lowered for marker in _INVITE_MARKERS) and not text.startswith("@"):
        return False
    if _NUMERIC_ENTITY_RE.fullmatch(text):
        return False
    return bool(_PUBLIC_USERNAME_RE.fullmatch(text))


def _normalize_public_handle(entity: str) -> str | None:
    if not _is_public_telegram_handle(entity):
        return None
    text = entity.strip()
    if "t.me/" in text.casefold() or "telegram.me/" in text.casefold():
        path = urlsplit(text).path if "://" in text else text
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        return f"@{slug.lstrip('@')}"
    return text if text.startswith("@") else f"@{text}"


def _public_locator_fields(
    *,
    kind: str,
    locator: str | None,
    spec: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, bool]:
    """Return (public_url, public_handle, is_private_identity)."""
    candidate = locator
    if not candidate and isinstance(spec, Mapping):
        for key in ("url", "feed_url", "base_url", "entity", "company"):
            raw = spec.get(key)
            if isinstance(raw, str) and raw.strip():
                candidate = raw.strip()
                break
    if not candidate:
        return None, None, False

    if kind.startswith("telegram"):
        handle = _normalize_public_handle(candidate)
        if handle is None:
            return None, None, True
        return f"https://t.me/{handle.lstrip('@')}", handle, False

    if _is_public_url(candidate):
        return candidate.strip(), None, False

    # Non-URL locators (e.g. lever company slug) stay as name only.
    return None, None, False


def _map_public_status(*, enabled: bool, listing: Mapping[str, Any]) -> PublicSourceStatus:
    if not enabled:
        return "disabled"
    raw_status = str(listing.get("status") or "").strip().casefold()
    degraded = bool(listing.get("degraded"))
    if degraded or raw_status in {"failing", "degraded", "paused", "unhealthy"}:
        return "degraded"
    if raw_status in {"pending", "candidate", "unknown", ""}:
        return "candidate"
    return "enabled"


def _sanitize_failure_reason(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if _SECRETISH_RE.search(text):
        return "redacted"
    text = _PATH_RE.sub("[path]", text)
    if len(text) > _MAX_FAILURE_REASON_LEN:
        text = text[: _MAX_FAILURE_REASON_LEN - 1].rstrip() + "…"
    return text


def _parser_route_summary(listing: Mapping[str, Any], spec: Mapping[str, Any] | None) -> str | None:
    requirements = listing.get("requirements")
    if isinstance(requirements, Mapping):
        reason = requirements.get("browser_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()[:80]
        if requirements.get("browser_required"):
            return "browser"

    assessment = listing.get("assessment")
    if isinstance(assessment, Mapping):
        monitors = assessment.get("recommended_monitors")
        if isinstance(monitors, list) and monitors:
            first = str(monitors[0]).strip()
            if first:
                return f"monitor={first}"[:80]

    if isinstance(spec, Mapping):
        monitor = spec.get("monitor")
        if isinstance(monitor, str) and monitor.strip() and monitor.strip() != "auto":
            return f"monitor={monitor.strip()}"[:80]
        scraper = spec.get("scraper")
        if isinstance(scraper, str) and scraper.strip():
            return f"scraper={scraper.strip()}"[:80]
    return None


def _redacted_source_id(kind: str, locator: str | None, original: str) -> str:
    material = f"{kind}:{locator or original}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:private_{digest}"


def sanitize_source_listing(listing: Mapping[str, Any]) -> PublicSourceRegistryEntry | None:
    """Project a private runtime listing row into a public-safe entry.

    Returns ``None`` when the source must be omitted entirely (e.g. local
    fixture paths). Private Telegram entities remain listed with a redacted
    identity and without handle/URL.
    """
    kind = str(listing.get("source_kind") or listing.get("kind") or "").strip()
    if not kind or kind in _HIDDEN_KINDS:
        return None

    enabled = bool(listing.get("enabled", True))
    original_id = str(listing.get("source_id") or "").strip()
    if not original_id:
        return None

    spec = listing.get("spec")
    spec_map = spec if isinstance(spec, Mapping) else None
    locator = listing.get("locator")
    locator_text = str(locator).strip() if isinstance(locator, str) and locator.strip() else None

    public_url, public_handle, private_identity = _public_locator_fields(
        kind=kind,
        locator=locator_text,
        spec=spec_map,
    )

    public_name = listing.get("source_name")
    name = str(public_name).strip() if isinstance(public_name, str) and public_name.strip() else None
    if private_identity:
        source_id = _redacted_source_id(kind, locator_text, original_id)
        name = kind.replace("_", " ")
        public_url = None
        public_handle = None
    else:
        source_id = original_id

    category = listing.get("category")
    region = listing.get("region")
    if isinstance(spec_map, Mapping):
        if category is None and isinstance(spec_map.get("category"), str):
            category = spec_map.get("category")
        if region is None and isinstance(spec_map.get("region"), str):
            region = spec_map.get("region")

    last_error = listing.get("last_error")
    failure = (
        None
        if enabled is False and not last_error
        else _sanitize_failure_reason(last_error)
    )

    return PublicSourceRegistryEntry(
        source_id=source_id,
        kind=kind,
        public_name=name,
        public_url=public_url,
        public_handle=public_handle,
        enabled=enabled,
        status=_map_public_status(enabled=enabled, listing=listing),
        category=str(category).strip() if isinstance(category, str) and category.strip() else None,
        region=str(region).strip() if isinstance(region, str) and region.strip() else None,
        last_success_at=_parse_datetime(listing.get("last_success_at")),
        last_checked_at=_parse_datetime(
            listing.get("last_run_at") or listing.get("last_started_at") or listing.get("last_checked_at")
        ),
        public_failure_reason=failure,
        parser_route_summary=_parser_route_summary(listing, spec_map),
    )


def build_public_source_registry(
    *,
    tenant_slug: str,
    listings: Sequence[Mapping[str, Any]],
    generated_at: datetime | None = None,
    status: PublicRegistryStatus = "ok",
    stale: bool = False,
    error: str | None = None,
) -> PublicSourceRegistry:
    """Sanitize runtime listings into a public registry envelope."""
    entries: list[PublicSourceRegistryEntry] = []
    for listing in listings:
        entry = sanitize_source_listing(listing)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: item.source_id)
    stamp = generated_at or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return PublicSourceRegistry(
        generated_at=stamp,
        tenant_slug=tenant_slug,
        source_count=len(entries),
        status=status,
        stale=stale,
        sources=tuple(entries),
        error=error,
    )


def public_registry_error(
    *,
    tenant_slug: str,
    message: str,
    status: PublicRegistryStatus = "error",
    stale: bool = False,
    generated_at: datetime | None = None,
) -> PublicSourceRegistry:
    """Explicit error/stale envelope without falling back to fixtures."""
    return build_public_source_registry(
        tenant_slug=tenant_slug,
        listings=(),
        generated_at=generated_at,
        status=status,
        stale=stale,
        error=message,
    )


def assert_public_safe_payload(payload: Mapping[str, Any]) -> None:
    """Raise AssertionError if a serialized registry still carries denylist keys."""

    def _walk(node: object, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_text = str(key)
                lower = key_text.casefold()
                if key_text in SENSITIVE_LISTING_KEYS or lower in SENSITIVE_LISTING_KEYS:
                    msg = f"sensitive key leaked at {path}.{key_text}"
                    raise AssertionError(msg)
                if lower in {
                    "password",
                    "secret",
                    "token",
                    "cookies",
                    "authorization",
                    "proxy_url",
                    "auth_source_id",
                    "api_key",
                }:
                    msg = f"sensitive key leaked at {path}.{key_text}"
                    raise AssertionError(msg)
                _walk(value, f"{path}.{key_text}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                _walk(item, f"{path}[{index}]")

    _walk(payload, "root")


async def list_public_sources_for_runner(
    runner: Any,
    tenant_slug: str,
    *,
    allowlist: frozenset[str] | None = None,
) -> PublicSourceRegistry:
    """Read runtime sources via ``runner.list_sources`` and sanitize them.

    ``runner`` is duck-typed to keep this helper free of circular imports; the
    production caller is ``TenantRunner.list_public_sources``.
    """
    if not is_public_tenant(tenant_slug, allowlist=allowlist):
        return public_registry_error(
            tenant_slug=tenant_slug,
            message="tenant is not published on the public source registry",
            status="error",
        )
    try:
        listings = await runner.list_sources(tenant_slug)
    except KeyError:
        return public_registry_error(
            tenant_slug=tenant_slug,
            message="tenant not found",
            status="error",
        )
    except Exception as exc:  # noqa: BLE001 - boundary: never invent fixture data
        return public_registry_error(
            tenant_slug=tenant_slug,
            message=f"runtime source listing failed: {type(exc).__name__}",
            status="error",
        )
    if not isinstance(listings, list):
        return public_registry_error(
            tenant_slug=tenant_slug,
            message="runtime source listing returned an unexpected shape",
            status="error",
        )
    return build_public_source_registry(tenant_slug=tenant_slug, listings=listings)
