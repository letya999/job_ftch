"""Local challenge engine: proof-of-work, interactive puzzle, HMAC clearance.

Defensive, loopback-only. Challenge tokens are opaque local artifacts: the engine
verifies them server-side and records only SHA-256 hash prefixes in the session
ledger. Raw token/cookie values are never written to artifacts.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from paritylab.models import ChallengeOutcome, ChallengeRecord, utc_now_iso

_PUZZLE_SHAPES = ("circle", "square", "triangle")
_PUZZLE_SIZE = 9
_TOKEN_VERSION = "p1"


def _hash_prefix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _leading_zero_bits(digest: bytes) -> int:
    count = 0
    for byte in digest:
        if byte == 0:
            count += 8
            continue
        for bit in range(7, -1, -1):
            if byte & (1 << bit):
                return count
            count += 1
        return count
    return count


@dataclass(slots=True)
class PowSpec:
    challenge_id: str
    session_id: str
    prefix: str
    difficulty_bits: int
    issued_at: float
    deadline: float
    max_attempts: int
    attempts: int = 0
    resolved: ChallengeOutcome = ChallengeOutcome.PENDING


@dataclass(slots=True)
class PuzzleSpec:
    challenge_id: str
    session_id: str
    shapes: tuple[str, ...]
    expected: tuple[int, ...]
    issued_at: float
    deadline: float
    attempts: int = 0
    resolved: ChallengeOutcome = ChallengeOutcome.PENDING

    def grid_svg(self) -> str:
        cells = []
        for index, shape in enumerate(self.shapes):
            x = (index % 3) * 60 + 30
            y = (index // 3) * 60 + 30
            if shape == "circle":
                cells.append(f'<circle cx="{x}" cy="{y}" r="20" fill="#2f6fed"/>')
            elif shape == "square":
                cells.append(f'<rect x="{x - 20}" y="{y - 20}" width="40" height="40" fill="#d64545"/>')
            else:
                cells.append(f'<polygon points="{x},{y - 22} {x - 22},{y + 18} {x + 22},{y + 18}" fill="#2f9d44"/>')
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" '
            f'viewBox="0 0 180 180">{"".join(cells)}</svg>'
        )


@dataclass(slots=True)
class ChallengeEngine:
    """Issues and verifies local challenges; never persists raw token values."""

    pow_ttl_seconds: float = 30.0
    puzzle_ttl_seconds: float = 120.0
    clearance_ttl_seconds: float = 600.0
    min_puzzle_duration_ms: float = 400.0
    clock: Callable[[], float] = field(default=time.time)
    rng: random.Random = field(default_factory=random.Random)
    _secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    _pow: dict[str, PowSpec] = field(default_factory=dict)
    _puzzles: dict[str, PuzzleSpec] = field(default_factory=dict)
    _revoked_jti: set[str] = field(default_factory=set)
    _pow_failures: dict[str, int] = field(default_factory=dict)
    _owner: dict[str, str] = field(default_factory=dict)
    ledger: list[ChallengeRecord] = field(default_factory=list)

    def issue_pow(self, session_id: str, difficulty_bits: int = 12) -> PowSpec:
        spec = PowSpec(
            challenge_id=uuid.uuid4().hex,
            session_id=session_id,
            prefix=secrets.token_hex(16),
            difficulty_bits=max(4, min(int(difficulty_bits), 24)),
            issued_at=self.clock(),
            deadline=self.clock() + self.pow_ttl_seconds,
            max_attempts=50,
        )
        self._pow[spec.challenge_id] = spec
        self._register(spec.challenge_id, session_id, "pow")
        return spec

    def pow_public(self, spec: PowSpec) -> dict[str, str | int | float]:
        return {
            "challenge_id": spec.challenge_id,
            "algorithm": "sha256",
            "prefix": spec.prefix,
            "difficulty_bits": spec.difficulty_bits,
            "deadline_seconds": max(0.0, spec.deadline - self.clock()),
        }

    def verify_pow(self, challenge_id: str, nonce: str) -> tuple[bool, str]:
        spec = self._pow.get(challenge_id)
        if spec is None:
            return False, "unknown_challenge"
        cid_hash = _hash_prefix(challenge_id)
        now = self.clock()
        if now > spec.deadline:
            self._resolve(cid_hash, "pow", ChallengeOutcome.EXPIRED, spec.attempts)
            return False, "expired"
        if spec.attempts >= spec.max_attempts:
            self._resolve(cid_hash, "pow", ChallengeOutcome.REJECTED, spec.attempts)
            return False, "too_many_attempts"
        spec.attempts += 1
        digest = hashlib.sha256(f"{spec.prefix}{nonce}".encode("utf-8")).digest()
        if _leading_zero_bits(digest) >= spec.difficulty_bits:
            self._resolve(cid_hash, "pow", ChallengeOutcome.VERIFIED, spec.attempts)
            return True, "verified"
        if spec.attempts >= spec.max_attempts:
            self._pow_failures[spec.session_id] = self._pow_failures.get(spec.session_id, 0) + 1
            self._resolve(cid_hash, "pow", ChallengeOutcome.REJECTED, spec.attempts)
            return False, "too_many_attempts"
        self._record(cid_hash, "pow", ChallengeOutcome.PENDING, spec.attempts)
        return False, "wrong_nonce"

    def pow_failure_count(self, session_id: str) -> int:
        return self._pow_failures.get(session_id, 0)

    def pow_spec(self, challenge_id: str) -> PowSpec | None:
        return self._pow.get(challenge_id)

    def puzzle_spec(self, challenge_id: str) -> PuzzleSpec | None:
        return self._puzzles.get(challenge_id)

    def issue_puzzle(self, session_id: str) -> PuzzleSpec:
        shapes = list(self.rng.choice(_PUZZLE_SHAPES) for _ in range(_PUZZLE_SIZE))
        if "circle" not in shapes:
            shapes[0] = "circle"
        if all(shape == "circle" for shape in shapes):
            shapes[-1] = "square"
        expected = tuple(index for index, shape in enumerate(shapes) if shape == "circle")
        spec = PuzzleSpec(
            challenge_id=uuid.uuid4().hex,
            session_id=session_id,
            shapes=tuple(shapes),
            expected=expected,
            issued_at=self.clock(),
            deadline=self.clock() + self.puzzle_ttl_seconds,
        )
        self._puzzles[spec.challenge_id] = spec
        self._register(spec.challenge_id, session_id, "puzzle")
        return spec

    def verify_puzzle(
        self,
        challenge_id: str,
        cells: object,
        *,
        duration_ms: float = 0.0,
        pointer_samples: int = 0,
    ) -> tuple[bool, str]:
        spec = self._puzzles.get(challenge_id)
        if spec is None:
            return False, "unknown_challenge"
        cid_hash = _hash_prefix(challenge_id)
        if self.clock() > spec.deadline:
            self._resolve(cid_hash, "puzzle", ChallengeOutcome.EXPIRED, spec.attempts)
            return False, "expired"
        spec.attempts += 1
        if not isinstance(cells, (list, tuple)):
            self._record(cid_hash, "puzzle", ChallengeOutcome.PENDING, spec.attempts)
            return False, "malformed_cells"
        try:
            submitted = frozenset(int(item) for item in cells)
        except (TypeError, ValueError):
            self._record(cid_hash, "puzzle", ChallengeOutcome.PENDING, spec.attempts)
            return False, "malformed_cells"
        if submitted != frozenset(spec.expected):
            self._record(cid_hash, "puzzle", ChallengeOutcome.PENDING, spec.attempts)
            return False, "wrong_selection"
        if duration_ms < self.min_puzzle_duration_ms or pointer_samples <= 0:
            self._record(cid_hash, "puzzle", ChallengeOutcome.PENDING, spec.attempts)
            return False, "implausible_behavior"
        self._resolve(cid_hash, "puzzle", ChallengeOutcome.VERIFIED, spec.attempts)
        return True, "verified"

    def issue_clearance(self, session_id: str) -> tuple[str, float]:
        sid_hash = _hash_prefix(session_id)
        jti = uuid.uuid4().hex
        exp = int(self.clock() + self.clearance_ttl_seconds)
        payload = f"{_TOKEN_VERSION}.{sid_hash}.{jti}.{exp}"
        signature = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        token = f"{payload}.{signature}"
        self._owner[_hash_prefix(token)] = session_id
        self._record(_hash_prefix(token), "clearance", ChallengeOutcome.VERIFIED, 0)
        return token, float(exp)

    def verify_clearance(self, token: str, session_id: str) -> tuple[bool, str]:
        parts = token.split(".")
        if len(parts) != 5 or parts[0] != _TOKEN_VERSION:
            return False, "malformed"
        _version, sid_hash, jti, raw_exp, signature = parts
        if sid_hash != _hash_prefix(session_id):
            self._owner.setdefault(_hash_prefix(token), session_id)
            self._record(_hash_prefix(token), "clearance", ChallengeOutcome.REJECTED, 1)
            return False, "session_mismatch"
        if jti in self._revoked_jti:
            return False, "revoked"
        payload = f"{_TOKEN_VERSION}.{sid_hash}.{jti}.{raw_exp}"
        expected = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            self._owner.setdefault(_hash_prefix(token), session_id)
            self._record(_hash_prefix(token), "clearance", ChallengeOutcome.REJECTED, 1)
            return False, "bad_signature"
        try:
            exp = int(raw_exp)
        except ValueError:
            return False, "malformed"
        if self.clock() > exp:
            return False, "expired"
        return True, "valid"

    def revoke_clearance(self, token: str) -> None:
        parts = token.split(".")
        if len(parts) == 5:
            self._revoked_jti.add(parts[2])

    def snapshot(self) -> Mapping[str, int]:
        return {
            "pow_open": sum(
                1 for item in self._pow.values() if item.resolved is ChallengeOutcome.PENDING
            ),
            "puzzle_open": sum(
                1 for item in self._puzzles.values() if item.resolved is ChallengeOutcome.PENDING
            ),
            "ledger_entries": len(self.ledger),
        }

    def drain_ledger(self, session_id: str) -> list[ChallengeRecord]:
        """Return and remove ledger records owned by the given session."""
        drained: list[ChallengeRecord] = []
        remaining: list[ChallengeRecord] = []
        for record in self.ledger:
            if self._owner.get(record.challenge_id_hash) == session_id:
                drained.append(record)
            else:
                remaining.append(record)
        self.ledger = remaining
        return drained

    def _register(self, challenge_id: str, session_id: str, kind: str) -> None:
        cid_hash = _hash_prefix(challenge_id)
        self._owner[cid_hash] = session_id
        self._record(cid_hash, kind, ChallengeOutcome.PENDING, 0)

    def _record(self, challenge_id_hash: str, kind: str, outcome: ChallengeOutcome, attempts: int) -> None:
        self.ledger.append(
            ChallengeRecord(
                challenge_id_hash=challenge_id_hash,
                kind=kind,
                issued_at=utc_now_iso(),
                outcome=outcome,
                attempts=attempts,
            )
        )

    def _resolve(self, challenge_id_hash: str, kind: str, outcome: ChallengeOutcome, attempts: int) -> None:
        self.ledger.append(
            ChallengeRecord(
                challenge_id_hash=challenge_id_hash,
                kind=kind,
                issued_at=utc_now_iso(),
                outcome=outcome,
                attempts=attempts,
                resolved_at=utc_now_iso(),
            )
        )
