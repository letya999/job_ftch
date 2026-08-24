"""Public-safe source registry contracts.

These models are intentionally narrow: only fields that may appear on a public
docs site or unauthenticated JSON endpoint. Private credentials, tenant/user
ids, raw specs, and debug metadata must never be added here.

Public source health statuses (``PublicSourceStatus``) are derived at read time
from runtime listing/health fields. They do not introduce a separate tracker:

- ``enabled``: source is on and recently observed healthy enough to publish
- ``disabled``: operator disabled the source
- ``degraded``: runtime degraded/failing/paused/error, or known error kind
- ``candidate``: pending / never checked / no recent health observation

``public_failure_reason`` prefers allowlisted machine codes (for example
``layout_changed``, ``auth_wall``, ``source_fetch_failed``) over free text.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic evaluates field annotations at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PublicSourceStatus = Literal["enabled", "disabled", "degraded", "candidate"]
PublicRegistryStatus = Literal["ok", "error", "stale"]


class PublicSourceRegistryEntry(BaseModel):
    """One public-safe source row for docs/API consumers.

    Diagnostics fields (``status``, timestamps, ``public_failure_reason``,
    ``parser_route_summary``) are health explainers only. They must stay free of
    tenant/user ids, cookies, tokens, proxy endpoints, browser profile paths,
    raw HTML/traces, resume data, and private Telegram identities.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    kind: str
    public_name: str | None = None
    public_url: str | None = None
    public_handle: str | None = None
    enabled: bool
    status: PublicSourceStatus
    category: str | None = None
    region: str | None = None
    last_success_at: datetime | None = None
    last_checked_at: datetime | None = None
    public_failure_reason: str | None = None
    parser_route_summary: str | None = None


class PublicSourceRegistry(BaseModel):
    """Envelope returned by the public source registry reader/export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    tenant_slug: str
    source_count: int = Field(ge=0)
    status: PublicRegistryStatus = "ok"
    stale: bool = False
    sources: tuple[PublicSourceRegistryEntry, ...] = ()
    error: str | None = None
