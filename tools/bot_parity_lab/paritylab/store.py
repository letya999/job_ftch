from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from paritylab.models import (
    BehaviorEvent,
    ChallengeRecord,
    Finding,
    GateDecisionRecord,
    IntentReport,
    OpaquePayloadRecord,
    ProtocolObservation,
    ProbeRecord,
    RequestRecord,
    ScoreSummary,
    SessionState,
    TLSFingerprint,
)

T = TypeVar("T")
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ArtifactStore:
    """In-memory session registry with atomic, reproducible JSON/Markdown output."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def validate_session_id(session_id: str) -> str:
        if not _SESSION_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        return session_id

    async def ensure_session(
        self,
        session_id: str,
        *,
        client_name: str = "unknown",
        client_family: str = "unknown",
        expected_failure: bool = False,
        gate_enabled: bool = False,
    ) -> SessionState:
        session_id = self.validate_session_id(session_id)
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = SessionState(
                    session_id=session_id,
                    client_name=client_name[:128],
                    client_family=client_family[:128],
                    expected_failure=expected_failure,
                    gate_enabled=gate_enabled,
                )
                self._sessions[session_id] = state
            else:
                if client_name != "unknown":
                    state.client_name = client_name[:128]
                if client_family != "unknown":
                    state.client_family = client_family[:128]
                state.expected_failure = expected_failure or state.expected_failure
                state.gate_enabled = gate_enabled or state.gate_enabled
            return state

    async def get(self, session_id: str) -> SessionState | None:
        session_id = self.validate_session_id(session_id)
        async with self._lock:
            return self._sessions.get(session_id)

    async def mutate(self, session_id: str, operation: Callable[[SessionState], T]) -> T:
        session_id = self.validate_session_id(session_id)
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = SessionState(
                    session_id=session_id,
                    client_name="unknown",
                    client_family="unknown",
                    expected_failure=False,
                    gate_enabled=False,
                )
                self._sessions[session_id] = state
            return operation(state)

    async def add_request(self, record: RequestRecord) -> None:
        await self.mutate(record.session_id, lambda state: state.requests.append(record))

    async def add_tls(self, session_id: str, record: TLSFingerprint) -> None:
        def append_unique(state: SessionState) -> None:
            if not any(
                item.connection_id == record.connection_id for item in state.tls_fingerprints
            ):
                state.tls_fingerprints.append(record)

        await self.mutate(session_id, append_unique)

    async def add_probe(self, record: ProbeRecord) -> None:
        await self.mutate(record.session_id, lambda state: state.probes.append(record))

    async def add_behavior(self, records: list[BehaviorEvent]) -> None:
        if not records:
            return
        await self.mutate(records[0].session_id, lambda state: state.behavior.extend(records))

    async def add_opaque(self, record: OpaquePayloadRecord) -> None:
        await self.mutate(record.session_id, lambda state: state.opaque_payloads.append(record))

    async def add_protocol_observation(self, record: ProtocolObservation) -> None:
        await self.mutate(
            record.session_id, lambda state: state.protocol_observations.append(record)
        )

    async def add_gate_decision(self, session_id: str, record: GateDecisionRecord) -> None:
        await self.mutate(session_id, lambda state: state.gate_decisions.append(record))

    async def add_challenge_records(self, session_id: str, records: list[ChallengeRecord]) -> None:
        if not records:
            return
        await self.mutate(session_id, lambda state: state.challenges.extend(records))

    async def record_trap_hit(self, session_id: str, path: str) -> None:
        await self.mutate(session_id, lambda state: state.trap_hits.append(path))

    async def set_intent(self, session_id: str, report: IntentReport) -> None:
        await self.mutate(session_id, lambda state: setattr(state, "intent", report))

    async def finalize(
        self,
        session_id: str,
        *,
        findings: list[Finding],
        summary: ScoreSummary,
        markdown: str,
    ) -> SessionState:
        def finish(state: SessionState) -> SessionState:
            from paritylab.models import utc_now_iso

            state.findings = findings
            state.summary = summary
            state.finished_at = utc_now_iso()
            return state

        state = await self.mutate(session_id, finish)
        session_dir = self.root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(session_dir / "raw.json", state.to_pretty_json() + "\n")
        self._atomic_write_text(session_dir / "report.md", markdown.rstrip() + "\n")
        self._write_ndjson(session_dir / "requests.ndjson", [item for item in state.requests])
        self._write_ndjson(session_dir / "behavior.ndjson", [item for item in state.behavior])
        self._write_ndjson(
            session_dir / "protocol.ndjson", [item for item in state.protocol_observations]
        )
        return state

    def _write_ndjson(self, path: Path, values: list[object]) -> None:
        from paritylab.models import json_safe

        text = "".join(
            json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True) + "\n"
            for value in values
        )
        self._atomic_write_text(path, text)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
