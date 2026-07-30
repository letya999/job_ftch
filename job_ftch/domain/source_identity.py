"""Typed source identity independent from transport and legacy SourceKind."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from job_ftch.domain.models import RawItem


class SourceFamily(StrEnum):
    TELEGRAM = "telegram"
    ATS_API = "ats_api"
    CAREER_WEB = "career_web"
    RSS = "rss"
    REST_API = "rest_api"
    FIXTURE = "fixture"
    REALTIME = "realtime"
    UNKNOWN = "unknown"


class ObservationKind(StrEnum):
    VACANCY_DETAIL = "vacancy_detail"
    LISTING = "listing"
    MESSAGE = "message"
    COMMENT = "comment"
    STRUCTURED_RECORD = "structured_record"
    UNKNOWN = "unknown"


class AcquisitionTransport(StrEnum):
    TELEGRAM_API = "telegram_api"
    HTTP = "http"
    BROWSER = "browser"
    WEBHOOK = "webhook"
    WEBSOCKET = "websocket"
    FIXTURE = "fixture"
    UNKNOWN = "unknown"


class SourceIdentity(BaseModel):
    """Source identity used by policy and calibration.

    ``legacy_kind`` preserves the new five-value enum during migration. It is
    never used as the source-family policy key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: SourceFamily = SourceFamily.UNKNOWN
    observation_kind: ObservationKind = ObservationKind.UNKNOWN
    transport: AcquisitionTransport = AcquisitionTransport.UNKNOWN
    adapter: str = Field(default="unknown", min_length=1)
    parser_version: str = Field(default="unknown", min_length=1)
    legacy_kind: str | None = None


def source_identity_for_parts(
    *,
    source_kind: object,
    source_name: str,
    metadata: dict[str, Any],
    source_identity: SourceIdentity | None = None,
) -> SourceIdentity:
    """Build identity for both raw and already-extracted records."""

    if source_identity is not None:
        return source_identity
    family = str(metadata.get("source_family") or "")
    legacy_kind = str(source_kind)
    if not family:
        family = {
            "telegram_channel": SourceFamily.TELEGRAM.value,
            "telegram_group": SourceFamily.TELEGRAM.value,
            "telegram_comment": SourceFamily.TELEGRAM.value,
            "debug": SourceFamily.FIXTURE.value,
            "career_site": SourceFamily.CAREER_WEB.value,
        }.get(legacy_kind, SourceFamily.UNKNOWN.value)
    kind = str(metadata.get("observation_kind") or ObservationKind.UNKNOWN.value)
    transport = str(metadata.get("transport") or AcquisitionTransport.UNKNOWN.value)
    try:
        family_value = SourceFamily(family)
    except ValueError:
        family_value = SourceFamily.UNKNOWN
    try:
        kind_value = ObservationKind(kind)
    except ValueError:
        kind_value = ObservationKind.UNKNOWN
    try:
        transport_value = AcquisitionTransport(transport)
    except ValueError:
        transport_value = AcquisitionTransport.UNKNOWN
    return SourceIdentity(
        family=family_value,
        observation_kind=kind_value,
        transport=transport_value,
        adapter=str(metadata.get("adapter") or source_name),
        parser_version=str(metadata.get("parser_version") or "unknown"),
        legacy_kind=legacy_kind,
    )


def source_identity_for_raw_item(item: RawItem) -> SourceIdentity:
    """Return identity for RawItem or normalized JobRecord compatibility values."""

    return source_identity_for_parts(
        source_kind=getattr(item, "source_kind", "unknown"),
        source_name=str(getattr(item, "source_name", "unknown")),
        metadata=dict(getattr(item, "metadata", {}) or {}),
        source_identity=getattr(item, "source_identity", None),
    )
