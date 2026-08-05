from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlencode

from paritylab.models import GateDisposition, JsonValue, json_safe


@dataclass(slots=True, frozen=True)
class ClientRunConfig:
    base_url: str
    artifacts_dir: Path
    headless: bool = True
    gate: bool = False
    expected_failure: bool = False
    timeout_seconds: float = 45.0
    baseline_profile: str = ""


@dataclass(slots=True)
class ClientRunResult:
    client_name: str
    client_family: str
    session_id: str
    skipped: bool = False
    skip_reason: str = ""
    disposition: GateDisposition = GateDisposition.SKIPPED
    score: int | None = None
    hard_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    artifact_dir: Path | None = None
    finding_codes: list[str] = field(default_factory=list)
    raw_response: dict[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def skipped_result(cls, name: str, family: str, reason: str) -> "ClientRunResult":
        return cls(client_name=name, client_family=family, session_id="", skipped=True, skip_reason=reason)


class ClientAdapter(Protocol):
    name: str
    family: str
    default_expected_failure: bool

    async def run(self, config: ClientRunConfig) -> ClientRunResult: ...


def new_session_id(prefix: str) -> str:
    safe_prefix = "".join(character if character.isalnum() else "-" for character in prefix).strip("-")
    return f"{safe_prefix}-{uuid.uuid4().hex[:20]}"


def build_target_url(
    config: ClientRunConfig, *, session_id: str, client_name: str, client_family: str
) -> str:
    query = urlencode(
        {
            "sid": session_id,
            "client": client_name,
            "family": client_family,
            "expected_failure": int(config.expected_failure),
            "gate": int(config.gate),
            "baseline_profile": config.baseline_profile,
        }
    )
    return f"{config.base_url}/?{query}"


def result_from_finish(
    *,
    name: str,
    family: str,
    session_id: str,
    payload: dict[str, Any],
) -> ClientRunResult:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    disposition_text = summary.get("disposition", GateDisposition.SKIPPED.value)
    try:
        disposition = GateDisposition(disposition_text)
    except ValueError:
        disposition = GateDisposition.SKIPPED
    raw = json_safe(payload)
    assert isinstance(raw, dict)
    return ClientRunResult(
        client_name=name,
        client_family=family,
        session_id=session_id,
        disposition=disposition,
        score=int(summary.get("score", 0)) if isinstance(summary, dict) else None,
        hard_count=int(summary.get("hard_count", 0)) if isinstance(summary, dict) else 0,
        medium_count=int(summary.get("medium_count", 0)) if isinstance(summary, dict) else 0,
        low_count=int(summary.get("low_count", 0)) if isinstance(summary, dict) else 0,
        artifact_dir=Path(str(payload["artifact_dir"])) if payload.get("artifact_dir") else None,
        finding_codes=[str(value) for value in payload.get("finding_codes", [])],
        raw_response=raw,
    )


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(slots=True, frozen=True)
class ClientHookContext:
    url: str
    finish_url: str
    session_id: str
    client_name: str
    client_family: str
    artifacts_dir: Path
    gate: bool
    expected_failure: bool
    timeout_seconds: float


ClientHook: type = Callable[[ClientHookContext], Any | Awaitable[Any]]
