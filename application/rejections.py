"""Explicit raw-item rejection flow for input hygiene."""

from __future__ import annotations

from typing import Any

from domain import QuarantinedRawItem, RawItem, RawItemRejectionReason


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
            source_kind=str(self.item.source_kind) if hasattr(self.item, "source_kind") else None,
            source_name=self.item.source_name if hasattr(self.item, "source_name") else None,
            external_id=self.item.external_id if hasattr(self.item, "external_id") else None,
            url=self.item.url if hasattr(self.item, "url") else None,
            snapshot=self.snapshot or _snapshot_item(self.item),
        )


def _snapshot_item(item: RawItem) -> dict[str, Any]:
    payload = item.model_dump(mode="json", warnings=False)
    raw_payload = payload.get("metadata", {}).pop("_raw_payload", None)
    if isinstance(raw_payload, dict):
        return raw_payload
    return payload
