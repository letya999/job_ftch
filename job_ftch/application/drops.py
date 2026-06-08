"""Explicit drop flow for pipeline stages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enum import StrEnum


class RawItemDropped(Exception):
    def __init__(
        self,
        *,
        reason: StrEnum,
        details: str,
        item: object,
        stage: str | None = None,
    ) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details
        self.item = item
        self.stage = stage
