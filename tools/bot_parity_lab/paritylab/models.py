from __future__ import annotations

import dataclasses
import enum
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def json_safe(value: Any) -> JsonValue:
    """Convert dataclasses and common containers to deterministic JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return json_safe(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return repr(value)


class SignalClass(str, enum.Enum):
    HARD_BOT = "hard_bot_signal"
    MEDIUM = "medium_suspicious"
    LOW = "low_entropy_mismatch"
    INFO = "informational"


class GateDisposition(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    EXPECTED_FAIL = "expected_fail"
    SKIPPED = "skipped"


class GateDecision(str, enum.Enum):
    ALLOW = "allow"
    JS_CHALLENGE = "js_challenge"
    INTERACTIVE_CHALLENGE = "interactive_challenge"
    DENY = "deny"
    TARPIT = "tarpit"


class ChallengeOutcome(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(slots=True, frozen=True)
class TLSFingerprint:
    connection_id: str
    observed_at: str
    client_host: str
    client_port: int
    backend_source_port: int
    record_version: int | None
    legacy_version: int | None
    supported_versions: tuple[int, ...]
    cipher_suites: tuple[int, ...]
    extension_ids: tuple[int, ...]
    supported_groups: tuple[int, ...]
    ec_point_formats: tuple[int, ...]
    signature_algorithms: tuple[int, ...]
    alpn_protocols: tuple[str, ...]
    server_name: str | None
    ja3_raw: str | None
    ja3: str | None
    ja4_raw: str | None
    ja4: str | None
    parse_error: str | None = None


@dataclass(slots=True, frozen=True)
class RequestRecord:
    request_id: str
    session_id: str
    observed_at: str
    monotonic_ns: int
    method: str
    path: str
    query: str
    scheme: str
    http_version: str
    client_host: str
    client_port: int
    connection_id: str | None
    tls_ja3: str | None
    tls_ja4: str | None
    headers: tuple[tuple[str, str], ...]
    response_status: int
    response_headers: tuple[tuple[str, str], ...]
    duration_ms: float
    request_body_bytes: int = 0
    response_body_bytes: int = 0

    def header_values(self, name: str) -> list[str]:
        wanted = name.lower()
        return [value for key, value in self.headers if key.lower() == wanted]

    def first_header(self, name: str) -> str | None:
        values = self.header_values(name)
        return values[0] if values else None


@dataclass(slots=True, frozen=True)
class ProbeRecord:
    session_id: str
    observed_at: str
    realm: str
    sequence: int
    data: dict[str, JsonValue]
    errors: tuple[dict[str, JsonValue], ...] = ()


@dataclass(slots=True, frozen=True)
class BehaviorEvent:
    session_id: str
    observed_at: str
    sequence: int
    event_type: str
    client_ts_ms: float
    since_navigation_ms: float
    trusted: bool | None
    data: dict[str, JsonValue]


@dataclass(slots=True, frozen=True)
class OpaquePayloadRecord:
    session_id: str
    observed_at: str
    request_id: str
    content_type: str
    body_bytes: int
    sha256: str
    shannon_entropy: float
    printable_ratio: float
    likely_base64: bool
    likely_json: bool
    key_shape: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ProtocolObservation:
    session_id: str
    observed_at: str
    protocol: str
    source: str
    parser_version: str
    fingerprint: str
    evidence: dict[str, JsonValue]


@dataclass(slots=True, frozen=True)
class Finding:
    signal_class: SignalClass
    severity_score: int
    code: str
    title: str
    reason: str
    evidence: dict[str, JsonValue] = field(default_factory=dict)
    realms: tuple[str, ...] = ()
    request_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ScoreSummary:
    score: int
    hard_count: int
    medium_count: int
    low_count: int
    info_count: int
    disposition: GateDisposition
    gate_reason: str


@dataclass(slots=True, frozen=True)
class GateDecisionRecord:
    observed_at: str
    request_path: str
    decision: GateDecision
    reason_code: str
    detail: str = ""


@dataclass(slots=True, frozen=True)
class ChallengeRecord:
    challenge_id_hash: str
    kind: str
    issued_at: str
    outcome: ChallengeOutcome
    attempts: int = 0
    resolved_at: str | None = None


@dataclass(slots=True, frozen=True)
class IntentReport:
    intent: str
    confidence: float
    trap_hits: int
    distinct_jobs: int
    listing_pages: int
    api_requests: int
    coverage_ratio: float
    velocity_rps: float
    median_gap_ms: float
    surfaces: dict[str, JsonValue] = field(default_factory=dict)
    evidence: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class SessionState:
    session_id: str
    client_name: str
    client_family: str
    expected_failure: bool
    gate_enabled: bool
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    requests: list[RequestRecord] = field(default_factory=list)
    tls_fingerprints: list[TLSFingerprint] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    behavior: list[BehaviorEvent] = field(default_factory=list)
    opaque_payloads: list[OpaquePayloadRecord] = field(default_factory=list)
    protocol_observations: list[ProtocolObservation] = field(default_factory=list)
    gate_decisions: list[GateDecisionRecord] = field(default_factory=list)
    challenges: list[ChallengeRecord] = field(default_factory=list)
    trap_hits: list[str] = field(default_factory=list)
    intent: IntentReport | None = None
    findings: list[Finding] = field(default_factory=list)
    summary: ScoreSummary | None = None

    def to_json_value(self) -> dict[str, JsonValue]:
        value = json_safe(self)
        assert isinstance(value, dict)
        return value

    def to_pretty_json(self) -> str:
        return json.dumps(self.to_json_value(), ensure_ascii=False, indent=2, sort_keys=True)
