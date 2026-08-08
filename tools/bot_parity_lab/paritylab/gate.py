"""Edge decision engine for the playground gate.

Models the decision layer of an Akamai/Cloudflare-style bot manager:
JA3/JA4 policy lists, sliding-window rate limiting, clearance requirements,
challenge escalation, and tarpit. Deterministic and loopback-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from paritylab.models import GateDecision, GateDecisionRecord, RequestRecord, utc_now_iso


@dataclass(frozen=True, slots=True)
class GatePolicy:
    require_clearance_prefixes: tuple[str, ...] = ("/api/jobs", "/jobs")
    ja3_deny: frozenset[str] = field(default_factory=frozenset)
    ja4_deny: frozenset[str] = field(default_factory=frozenset)
    ja3_challenge: frozenset[str] = field(default_factory=frozenset)
    window_seconds: float = 5.0
    max_requests_per_window: int = 40
    tarpit_multiplier: float = 2.5
    tarpit_delay_ms: int = 1200
    pow_failure_escalation: int = 1
    challenge_exempt_paths: tuple[str, ...] = ("/robots.txt", "/favicon.ico")
    medium_risk_challenge_threshold: int = 2

    def requires_clearance(self, path: str) -> bool:
        return any(path == prefix or path.startswith(prefix) for prefix in self.require_clearance_prefixes)


@dataclass(slots=True)
class GateEngine:
    policy: GatePolicy = field(default_factory=GatePolicy)

    def decide(
        self,
        *,
        path: str,
        requests: Sequence[RequestRecord],
        ja3: str | None,
        ja4: str | None,
        clearance_valid: bool,
        pow_failures: int,
        hard_risk_codes: Sequence[str] = (),
        medium_risk_codes: Sequence[str] = (),
    ) -> GateDecisionRecord:
        policy = self.policy
        if path in policy.challenge_exempt_paths:
            return self._record(path, GateDecision.ALLOW, "EXEMPT_PATH")

        if ja3 and ja3 in policy.ja3_deny:
            return self._record(path, GateDecision.DENY, "JA3_DENYLIST")
        if ja4 and ja4 in policy.ja4_deny:
            return self._record(path, GateDecision.DENY, "JA4_DENYLIST")

        if hard_risk_codes:
            return self._record(
                path,
                GateDecision.DENY,
                "LIVE_HARD_RISK",
                detail=",".join(sorted(set(hard_risk_codes))),
            )

        window_ns = int(policy.window_seconds * 1_000_000_000)
        if requests:
            latest = max(request.monotonic_ns for request in requests)
            recent = sum(
                1 for request in requests if latest - request.monotonic_ns <= window_ns
            )
            if recent > policy.max_requests_per_window * policy.tarpit_multiplier:
                return self._record(
                    path,
                    GateDecision.TARPIT,
                    "RATE_BURST",
                    detail=f"requests_in_window={recent}",
                )
            if recent > policy.max_requests_per_window:
                return self._record(
                    path,
                    GateDecision.INTERACTIVE_CHALLENGE,
                    "RATE_LIMIT",
                    detail=f"requests_in_window={recent}",
                )

        if not policy.requires_clearance(path):
            if ja3 and ja3 in policy.ja3_challenge:
                return self._record(path, GateDecision.JS_CHALLENGE, "JA3_CHALLENGE_LIST")
            return self._record(path, GateDecision.ALLOW, "NO_POLICY_MATCH")

        if len(set(medium_risk_codes)) >= policy.medium_risk_challenge_threshold:
            return self._record(
                path,
                GateDecision.INTERACTIVE_CHALLENGE,
                "LIVE_MEDIUM_RISK",
                detail=",".join(sorted(set(medium_risk_codes))),
            )
        if clearance_valid:
            return self._record(path, GateDecision.ALLOW, "CLEARANCE_VALID")
        if pow_failures >= policy.pow_failure_escalation:
            return self._record(path, GateDecision.INTERACTIVE_CHALLENGE, "CHALLENGE_ESCALATION")
        if ja3 and ja3 in policy.ja3_challenge:
            return self._record(path, GateDecision.INTERACTIVE_CHALLENGE, "JA3_CHALLENGE_LIST")
        return self._record(path, GateDecision.JS_CHALLENGE, "CLEARANCE_MISSING")

    @staticmethod
    def _record(
        path: str, decision: GateDecision, reason_code: str, *, detail: str = ""
    ) -> GateDecisionRecord:
        return GateDecisionRecord(
            observed_at=utc_now_iso(),
            request_path=path,
            decision=decision,
            reason_code=reason_code,
            detail=detail,
        )
