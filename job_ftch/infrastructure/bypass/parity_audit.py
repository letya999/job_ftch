from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BLOCKING_SIGNAL_CLASSES = {"hard_bot_signal", "medium_suspicious"}


SURFACE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("TLS_", "tls"),
    ("HTTP", "transport"),
    ("NET_", "network"),
    ("JS_", "runtime"),
    ("CDP_", "cdp"),
    ("REALM_", "realm"),
    ("BEHAVIOR_", "behavior"),
    ("IP_", "reputation"),
    ("OPAQUE_", "opaque_payload"),
)


@dataclass(slots=True, frozen=True)
class ParityFinding:
    code: str
    signal_class: str
    surface: str
    severity_score: int
    title: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_gate(self) -> bool:
        return self.signal_class in BLOCKING_SIGNAL_CLASSES


@dataclass(slots=True, frozen=True)
class ParityAuditSummary:
    client_name: str
    session_id: str
    score: int
    disposition: str
    hard_count: int
    medium_count: int
    low_count: int
    findings: tuple[ParityFinding, ...]

    @property
    def ok(self) -> bool:
        return self.disposition == "pass" and not any(
            finding.blocks_gate for finding in self.findings
        )

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings if finding.blocks_gate)

    @property
    def surface_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.surface] = counts.get(finding.surface, 0) + 1
        return counts


def classify_parity_surface(code: str) -> str:
    normalized = code.upper()
    for prefix, surface in SURFACE_PREFIXES:
        if normalized.startswith(prefix):
            return surface
    if "WEBDRIVER" in normalized or "HEADLESS" in normalized:
        return "runtime"
    if "HEADER" in normalized or "FETCH" in normalized or "UA" in normalized:
        return "network"
    return "unknown"


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _finding_from_json(value: dict[str, Any]) -> ParityFinding:
    code = str(value.get("code") or "UNKNOWN")
    signal_class = str(value.get("signal_class") or value.get("severity") or "informational")
    return ParityFinding(
        code=code,
        signal_class=signal_class,
        surface=classify_parity_surface(code),
        severity_score=_coerce_int(value.get("severity_score")),
        title=str(value.get("title") or code),
        reason=str(value.get("reason") or value.get("detail") or ""),
        evidence=dict(value.get("evidence") or {}),
    )


def summarize_parity_payload(payload: dict[str, Any]) -> ParityAuditSummary:
    raw_summary = payload.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    findings = tuple(
        _finding_from_json(item) for item in payload.get("findings", []) if isinstance(item, dict)
    )
    return ParityAuditSummary(
        client_name=str(payload.get("client_name") or payload.get("client") or ""),
        session_id=str(payload.get("session_id") or ""),
        score=_coerce_int(summary.get("score")),
        disposition=str(summary.get("disposition") or "skipped"),
        hard_count=_coerce_int(summary.get("hard_count")),
        medium_count=_coerce_int(summary.get("medium_count")),
        low_count=_coerce_int(summary.get("low_count")),
        findings=findings,
    )


def load_parity_raw(path: str | Path) -> ParityAuditSummary:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Parity artifact must contain a JSON object: {path}"
        raise ValueError(msg)
    return summarize_parity_payload(payload)
