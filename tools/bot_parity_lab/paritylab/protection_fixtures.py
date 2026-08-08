"""Owned, inert protection-page fixtures for detector regression tests.

These pages contain only local marker text. They never embed provider scripts,
issue clearance cookies, accept CAPTCHA tokens, or implement a solve path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ProtectionFixture:
    fixture_id: str
    status_code: int
    headers: Mapping[str, str]
    body: str
    contract: ChallengeContract | None = None


@dataclass(frozen=True, slots=True)
class ChallengeContract:
    """Safe local model of a protection decision, never a solve protocol."""

    challenge_type: str
    synthetic_sitekey: str | None
    action: str | None
    min_score: float | None
    deadline_reserve_seconds: float
    response_action: str = "manual_required"
    provider_task_created: bool = False

    def public_payload(self) -> dict[str, str | float | bool | None]:
        return {
            "challenge_type": self.challenge_type,
            "synthetic_sitekey": self.synthetic_sitekey,
            "action": self.action,
            "min_score": self.min_score,
            "deadline_reserve_seconds": self.deadline_reserve_seconds,
            "response_action": self.response_action,
            "provider_task_created": self.provider_task_created,
        }


def _page(title: str, content: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body>{content}</body></html>"


FIXTURES: Mapping[str, ProtectionFixture] = MappingProxyType(
    {
        "waf_block": ProtectionFixture(
            fixture_id="waf_block",
            status_code=403,
            headers={"x-parity-protection": "owned-waf-block"},
            body=_page("Owned WAF fixture", "Automated browser fingerprint rejected by owned fixture."),
        ),
        "captcha_recaptcha": ProtectionFixture(
            fixture_id="captcha_recaptcha",
            status_code=200,
            headers={"x-parity-protection": "owned-captcha-fixture"},
            body=_page(
                "Owned CAPTCHA fixture",
                '<main><p>Owned test fixture only.</p><div class="g-recaptcha" '
                'data-sitekey="PARITY_TEST_SITEKEY_NOT_VALID" data-action="jobs_search" '
                'data-min-score="0.7"></div></main>',
            ),
            contract=ChallengeContract(
                challenge_type="recaptcha",
                synthetic_sitekey="PARITY_TEST_SITEKEY_NOT_VALID",
                action="jobs_search",
                min_score=0.7,
                deadline_reserve_seconds=20.0,
            ),
        ),
        "passive_challenge": ProtectionFixture(
            fixture_id="passive_challenge",
            status_code=200,
            headers={"x-parity-protection": "owned-passive-challenge"},
            body=_page("Owned challenge fixture", "Checking your browser. Owned local challenge fixture."),
            contract=ChallengeContract(
                challenge_type="passive_browser_challenge",
                synthetic_sitekey=None,
                action=None,
                min_score=None,
                deadline_reserve_seconds=8.0,
            ),
        ),
        "qrator_jsid": ProtectionFixture(
            fixture_id="qrator_jsid",
            status_code=200,
            headers={"x-qrator-requestid": "owned-fixture"},
            body=_page(
                "Owned Qrator fixture",
                "Qrator Labs fixture jsid document.cookie window.location.reload",
            ),
            contract=ChallengeContract(
                challenge_type="qrator_jsid",
                synthetic_sitekey=None,
                action=None,
                min_score=None,
                deadline_reserve_seconds=15.0,
            ),
        ),
    }
)


def get_fixture(fixture_id: str) -> ProtectionFixture | None:
    return FIXTURES.get(fixture_id)
