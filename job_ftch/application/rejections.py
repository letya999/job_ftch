"""Explicit raw-item rejection flow for input hygiene."""

from __future__ import annotations

from typing import Any

from job_ftch.domain import QuarantinedRawItem, RawItem, RawItemRejectionReason


class RawItemRejected(Exception):
    def __init__(
        self,
        *,
        reason: RawItemRejectionReason,
        details: str,
        item: RawItem,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details
        self.item = item
        self.snapshot = snapshot or {}

    def to_quarantined(self) -> QuarantinedRawItem:
        return QuarantinedRawItem(
            reason=self.reason,
            details=self.details,
            source_kind=str(self.item.source_kind),
            source_name=self.item.source_name,
            external_id=self.item.external_id,
            url=self.item.url,
            snapshot=self.snapshot or _snapshot_item(self.item),
        )


def _snapshot_item(item: RawItem) -> dict[str, Any]:
    payload = item.model_dump(mode="json", warnings=False)
    raw_payload = payload.get("metadata", {}).pop("_raw_payload", None)
    if isinstance(raw_payload, dict):
        return raw_payload
    return payload
