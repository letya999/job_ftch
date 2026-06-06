"""Explicit low-cost drop flow for early triage."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain import RawItem
    from domain.triage import TriageRejectionReason


class RawItemDropped(Exception):
    def __init__(
        self,
        *,
        reason: TriageRejectionReason,
        details: str,
        item: RawItem,
    ) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details
        self.item = item
