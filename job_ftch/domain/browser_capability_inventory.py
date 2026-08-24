"""Public-safe browser/bypass capability inventory contracts.

Read-only models for agent/MCP/API consumers. Never include cookies, tokens,
proxy URLs, browser profile paths, executable paths, or secret values.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic evaluates annotations at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CapabilityAvailability = Literal["available", "unavailable", "disabled", "degraded"]
CapabilityGroup = Literal[
    "direct_http",
    "stealth_http_tls",
    "browser",
    "persistent_session",
    "proxy_backed",
    "manual_challenge",
    "disabled_unavailable",
]
CapabilityRisk = Literal["low", "medium", "high", "critical"]
RouteDiagnosticStatus = Literal["selected", "available", "unavailable", "skipped", "blocked"]


class RequiredSecretState(BaseModel):
    """Redacted presence of a required provider secret or config label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    present: bool


class BrowserCapabilityEntry(BaseModel):
    """One public-safe capability row for inventory consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    group: CapabilityGroup
    availability: CapabilityAvailability
    reason: str | None = None
    cost: int = Field(ge=0)
    risk: CapabilityRisk
    required_providers: tuple[str, ...] = ()
    required_secrets: tuple[RequiredSecretState, ...] = ()
    supports_js: bool = False
    supports_session: bool = False
    supports_proxy: bool = False
    hard_timeout_seconds: float | None = Field(default=None, ge=0.0)
    max_concurrency: int | None = Field(default=None, ge=0)
    description: str
    requires_approval: bool = False
    engine: str | None = None
    legal_gate: str | None = None


class BrowserCapabilityInventory(BaseModel):
    """Envelope returned by the browser/bypass capability inventory reader."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    status: Literal["ok", "error"] = "ok"
    capability_count: int = Field(ge=0)
    fallback_order: tuple[str, ...] = ()
    capabilities: tuple[BrowserCapabilityEntry, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None


class RouteCapabilityDiagnostic(BaseModel):
    """Why a single capability is selected, available, or unavailable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    group: CapabilityGroup
    status: RouteDiagnosticStatus
    reason: str
    cost: int = Field(ge=0)
    risk: CapabilityRisk
    engine: str | None = None


class RoutePlanExplanation(BaseModel):
    """Route planner diagnostics for a source/spec without executing browsers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    source_id: str | None = None
    source_kind: str | None = None
    requested_bypass: str | None = None
    selected_capability_id: str | None = None
    selected_group: CapabilityGroup | None = None
    fallback_order: tuple[str, ...] = ()
    diagnostics: tuple[RouteCapabilityDiagnostic, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None
