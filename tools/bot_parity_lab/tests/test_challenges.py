from __future__ import annotations

import hashlib
import random

from paritylab.challenges import ChallengeEngine
from paritylab.models import ChallengeOutcome


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _pow_nonce(prefix: str, difficulty_bits: int) -> str:
    for nonce in range(100_000):
        digest = hashlib.sha256(f"{prefix}{nonce}".encode("utf-8")).digest()
        bits = 0
        for byte in digest:
            if byte == 0:
                bits += 8
                continue
            for bit in range(7, -1, -1):
                if byte & (1 << bit):
                    break
                bits += 1
            break
        if bits >= difficulty_bits:
            return str(nonce)
    raise AssertionError("test PoW nonce not found")


def test_pow_success_and_ledger_drain() -> None:
    clock = Clock()
    engine = ChallengeEngine(clock=clock)
    spec = engine.issue_pow("session-a", difficulty_bits=4)

    ok, reason = engine.verify_pow(spec.challenge_id, _pow_nonce(spec.prefix, spec.difficulty_bits))

    assert (ok, reason) == (True, "verified")
    drained = engine.drain_ledger("session-a")
    assert [item.outcome for item in drained] == [ChallengeOutcome.PENDING, ChallengeOutcome.VERIFIED]
    assert all(len(item.challenge_id_hash) == 16 for item in drained)
    assert spec.challenge_id not in str(drained)


def test_pow_wrong_nonce_escalates_after_max_attempts() -> None:
    engine = ChallengeEngine()
    spec = engine.issue_pow("session-a", difficulty_bits=24)
    spec.max_attempts = 2

    assert engine.verify_pow(spec.challenge_id, "bad-1") == (False, "wrong_nonce")
    assert engine.verify_pow(spec.challenge_id, "bad-2") == (False, "too_many_attempts")

    assert engine.pow_failure_count("session-a") == 1


def test_puzzle_requires_correct_cells_and_plausible_behavior() -> None:
    engine = ChallengeEngine(rng=random.Random(7), min_puzzle_duration_ms=400.0)
    spec = engine.issue_puzzle("session-a")

    assert engine.verify_puzzle(spec.challenge_id, [], duration_ms=500, pointer_samples=2) == (
        False,
        "wrong_selection",
    )
    assert engine.verify_puzzle(
        spec.challenge_id,
        list(spec.expected),
        duration_ms=10,
        pointer_samples=2,
    ) == (False, "implausible_behavior")
    assert engine.verify_puzzle(
        spec.challenge_id,
        list(spec.expected),
        duration_ms=500,
        pointer_samples=2,
    ) == (True, "verified")


def test_clearance_validates_session_signature_expiry_and_revocation() -> None:
    clock = Clock()
    engine = ChallengeEngine(clock=clock, clearance_ttl_seconds=10)
    token, _expires = engine.issue_clearance("session-a")

    assert engine.verify_clearance(token, "session-a") == (True, "valid")
    assert engine.verify_clearance(token, "session-b") == (False, "session_mismatch")
    assert engine.verify_clearance(f"{token}tampered", "session-a") == (False, "bad_signature")

    engine.revoke_clearance(token)
    assert engine.verify_clearance(token, "session-a") == (False, "revoked")

    token2, _expires2 = engine.issue_clearance("session-a")
    clock.advance(11)
    assert engine.verify_clearance(token2, "session-a") == (False, "expired")
