from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from job_ftch.infrastructure.bypass.captcha_providers import register_captcha_provider
from job_ftch.infrastructure.bypass.captcha_solver import (
    CaptchaFailureReason,
    CaptchaSolverBypass,
    CaptchaSolveResult,
    _create_captcha_solver,
)
from job_ftch.infrastructure.sources.browser_utils import navigate
from job_ftch.infrastructure.sources.source_deadline import (
    reset_source_deadline,
    set_source_deadline,
)


class _FixturePage:
    def __init__(self, html: str = "") -> None:
        self._html = html
        self.url = "https://example.test/jobs"
        self.evaluate = AsyncMock(return_value=True)

    async def content(self) -> str:
        return self._html


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<div class="cf-turnstile" data-sitekey="0x1"></div>', "cloudflare"),
        ('<div class="h-captcha" data-sitekey="h1"></div>', "hcaptcha"),
        ('<div class="g-recaptcha" data-sitekey="r1"></div>', "recaptcha"),
        ('<script>window.dd="datadome"</script>', "datadome"),
        ('<div id="px-captcha">perimeterx</div>', "perimeterx"),
        ('<img class="captcha-image" src="/captcha.png">', "image"),
    ],
)
@pytest.mark.asyncio
async def test_offline_challenge_fixtures_are_detected(html: str, expected: str) -> None:
    solver = CaptchaSolverBypass(wait_seconds=0.01)
    assert await solver._detect_challenge(_FixturePage(html)) == expected


@pytest.mark.asyncio
async def test_missing_paid_credential_is_graceful() -> None:
    solver = CaptchaSolverBypass(provider="capsolver", api_key="")
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.MISSING_CREDENTIAL


@pytest.mark.asyncio
async def test_solver_attempt_budget_is_enforced() -> None:
    solver = CaptchaSolverBypass(provider="capsolver", api_key="", max_attempts=1)
    await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_paid_provider_is_rejected_when_deadline_is_too_short() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="secret-for-test",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    token = set_source_deadline(asyncio.get_running_loop().time() + 0.1)
    try:
        result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    finally:
        reset_source_deadline(token)
    assert result.failure_reason is CaptchaFailureReason.DEADLINE_INSUFFICIENT


@pytest.mark.asyncio
async def test_paid_calls_are_serialized_per_solver() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="secret-for-test",  # pragma: allowlist secret
        max_attempts=2,
        max_paid_attempts=2,
    )
    concurrent = 0
    peak = 0

    async def fake_external(*args, **kwargs):
        nonlocal concurrent, peak
        del args, kwargs
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        concurrent -= 1
        return CaptchaSolveResult(solved=False, method="capsolver")

    solver._solve_external_api = fake_external  # type: ignore[method-assign]
    await asyncio.gather(
        solver.solve(_FixturePage(), challenge_type="hcaptcha"),
        solver.solve(_FixturePage(), challenge_type="hcaptcha"),
    )
    assert peak == 1


@pytest.mark.parametrize(
    ("provider_name", "provider_result", "expected_reason"),
    [
        (
            "fake-success",
            CaptchaSolveResult(solved=True, method="fake-success"),
            None,
        ),
        (
            "fake-timeout",
            CaptchaSolveResult(
                solved=False,
                method="fake-timeout",
                failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
            ),
            CaptchaFailureReason.PROVIDER_TIMEOUT,
        ),
        (
            "fake-rejection",
            CaptchaSolveResult(
                solved=False,
                method="fake-rejection",
                failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
            ),
            CaptchaFailureReason.PROVIDER_REJECTED,
        ),
        (
            "fake-malformed",
            {"token": "not-a-typed-result"},
            CaptchaFailureReason.PROVIDER_UNAVAILABLE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_outcome_matrix(
    provider_name: str,
    provider_result: object,
    expected_reason: CaptchaFailureReason | None,
) -> None:
    class FakeProvider:
        def __init__(self, api_key: str) -> None:
            assert api_key == "offline-test-key"  # pragma: allowlist secret

        async def solve(self, page, *, challenge_type: str, url: str):
            del page, challenge_type, url
            return provider_result

    register_captcha_provider(provider_name)(FakeProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider=provider_name,
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is expected_reason
    assert result.solved is (expected_reason is None)


@pytest.mark.asyncio
async def test_disabled_provider_skips_external_call() -> None:
    calls = 0

    class SpyProvider:
        def __init__(self, api_key: str) -> None:
            del api_key

        async def solve(self, page, *, challenge_type: str, url: str):
            nonlocal calls
            calls += 1
            return CaptchaSolveResult(solved=True, method="spy")

    register_captcha_provider("spy_disabled")(SpyProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider="spy_disabled",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
        enabled_providers=frozenset({"browser_wait", "nopecha"}),
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.PROVIDER_DISABLED
    assert result.solved is False
    assert calls == 0


@pytest.mark.asyncio
async def test_enabled_provider_still_fires() -> None:
    class OkProvider:
        def __init__(self, api_key: str) -> None:
            del api_key

        async def solve(self, page, *, challenge_type: str, url: str):
            return CaptchaSolveResult(solved=True, method="ok_enabled")

    register_captcha_provider("ok_enabled")(OkProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider="ok_enabled",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
        enabled_providers=frozenset({"browser_wait", "ok_enabled"}),
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.solved is True
    assert result.method == "ok_enabled"


@pytest.mark.asyncio
async def test_unknown_provider_is_graceful() -> None:
    solver = CaptchaSolverBypass(
        provider="not-registered",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
async def test_solver_logs_never_include_token_cookie_or_provider_secret() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="provider-secret",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    solver._detect_challenge = AsyncMock(return_value="hcaptcha")  # type: ignore[method-assign]
    solver.solve = AsyncMock(  # type: ignore[method-assign]
        return_value=CaptchaSolveResult(
            solved=True,
            method="capsolver",
            tokens={"captcha_token": "token-secret"},
            cookies={"cf_clearance": "cookie-secret"},
        )
    )
    with capture_logs() as logs:
        await solver.apply_page(_FixturePage())
    rendered = str(logs)
    assert "provider-secret" not in rendered
    assert "token-secret" not in rendered
    assert "cookie-secret" not in rendered


def test_factory_reads_provider_key_from_environment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSOLVER_API_KEY", "env-test-key")
    solver = _create_captcha_solver({"provider": "capsolver", "api_key": "must-not-win"})
    assert solver._api_key == "env-test-key"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_navigate_solves_in_current_page_before_reloading() -> None:
    responses = iter([SimpleNamespace(status=503), SimpleNamespace(status=200)])
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=lambda *args, **kwargs: next(responses)),
    )
    controller = SimpleNamespace(solve_page_challenge=AsyncMock(return_value=True))

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_awaited_once_with(
        page,
        url="https://example.test/jobs",
    )
    assert page.goto.await_count == 2
