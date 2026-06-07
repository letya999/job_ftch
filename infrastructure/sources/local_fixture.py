"""Local debug source backed by JSON or JSONL fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from application.registry import register_source, register_source_v2
from domain import QuarantinedRawItem, RawItem, RawItemRejectionReason

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from application.contracts import AuthProvider
    from config import Settings
    from domain.source_spec import LocalFixtureSpec


class LocalFixtureSource:
    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        items = (
            self._iter_jsonl_payloads()
            if self._fixture_path.suffix == ".jsonl"
            else self._iter_json_payloads()
        )
        for item in items:
            yield item

    def _iter_json_payloads(self) -> list[RawItem | QuarantinedRawItem]:
        raw_text = self._fixture_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return []
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return [self._json_decode_error(exc, raw_text=raw_text)]
        if not isinstance(data, list):
            return [
                QuarantinedRawItem(
                    reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                    details="Local fixture source expects a JSON array or JSONL file.",
                    snapshot={"fixture_path": str(self._fixture_path)},
                )
            ]
        return [
            self._validate_payload(payload, index=index)
            for index, payload in enumerate(data, start=1)
        ]

    def _iter_jsonl_payloads(self) -> list[RawItem | QuarantinedRawItem]:
        items: list[RawItem | QuarantinedRawItem] = []
        raw_text = self._fixture_path.read_text(encoding="utf-8")
        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                items.append(
                    self._json_decode_error(
                        exc,
                        raw_line=line,
                        line_number=line_number,
                    )
                )
                continue
            items.append(self._validate_payload(payload, index=line_number))
        return items

    def _validate_payload(self, payload: Any, *, index: int) -> RawItem | QuarantinedRawItem:
        if not isinstance(payload, dict):
            return QuarantinedRawItem(
                reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                details="Fixture payload must be a JSON object.",
                snapshot={"payload": payload, "record_index": index},
            )
        try:
            return RawItem.model_validate(payload)
        except ValidationError as exc:
            return QuarantinedRawItem(
                reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                details=str(exc),
                source_kind=(
                    str(payload.get("source_kind"))
                    if payload.get("source_kind") is not None
                    else None
                ),
                source_name=(
                    str(payload.get("source_name"))
                    if payload.get("source_name") is not None
                    else None
                ),
                external_id=(
                    str(payload.get("external_id"))
                    if payload.get("external_id") is not None
                    else None
                ),
                snapshot={"payload": payload, "record_index": index},
            )

    def _json_decode_error(
        self,
        exc: json.JSONDecodeError,
        *,
        raw_text: str | None = None,
        raw_line: str | None = None,
        line_number: int | None = None,
    ) -> QuarantinedRawItem:
        snapshot: dict[str, Any] = {"fixture_path": str(self._fixture_path)}
        if raw_text is not None:
            snapshot["raw_text"] = raw_text
        if raw_line is not None:
            snapshot["raw_line"] = raw_line
        if line_number is not None:
            snapshot["line_number"] = line_number
        return QuarantinedRawItem(
            reason=RawItemRejectionReason.INVALID_RAW_ITEM,
            details=f"Invalid JSON fixture payload: {exc}",
            snapshot=snapshot,
        )


@register_source("local_fixture")
def _build_local_fixture_source(settings: Settings) -> LocalFixtureSource:
    return LocalFixtureSource(settings.debug_source_path)


@register_source_v2("local_fixture")
def _build_local_fixture_source_v2(
    spec: LocalFixtureSpec,
    auth: AuthProvider,
) -> LocalFixtureSource:
    del auth
    return LocalFixtureSource(Path(spec.path))
