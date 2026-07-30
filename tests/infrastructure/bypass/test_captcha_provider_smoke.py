"""Explicitly opt-in paid provider smoke test; excluded from normal offline cost."""

from __future__ import annotations

import os

import pytest

from job_ftch.infrastructure.bypass.captcha_providers import resolve_captcha_provider

pytestmark = [pytest.mark.network, pytest.mark.paid_provider]


class _SmokePage:
    def __init__(self, url: str, sitekey: str) -> None:
        self.url = url
        self._sitekey = sitekey

    async def evaluate(self, script: str) -> str:
        del script
        return self._sitekey


@pytest.mark.asyncio
async def test_configured_paid_provider_can_return_a_token() -> None:
    if os.environ.get("JOB_FTCH_RUN_PAID_PROVIDER_SMOKE") != "1":
        pytest.skip("set JOB_FTCH_RUN_PAID_PROVIDER_SMOKE=1 to allow provider cost")
    provider_name = os.environ.get("CAPTCHA_SMOKE_PROVIDER", "capsolver")
    key_env = {
        "capsolver": "CAPSOLVER_API_KEY",
        "2captcha": "TWOCAPTCHA_API_KEY",
        "anticaptcha": "ANTICAPTCHA_API_KEY",
    }[provider_name]
    api_key = os.environ.get(key_env, "")
    url = os.environ.get("CAPTCHA_SMOKE_URL", "")
    sitekey = os.environ.get("CAPTCHA_SMOKE_SITEKEY", "")
    challenge_type = os.environ.get("CAPTCHA_SMOKE_CHALLENGE", "hcaptcha")
    if not api_key or not url or not sitekey:
        pytest.skip(f"{key_env}, CAPTCHA_SMOKE_URL and CAPTCHA_SMOKE_SITEKEY are required")

    result = await resolve_captcha_provider(provider_name, api_key).solve(
        _SmokePage(url, sitekey),
        challenge_type=challenge_type,
        url=url,
    )
    assert result.solved, (result.failure_reason, result.error)
