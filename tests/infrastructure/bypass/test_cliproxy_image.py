"""CLIProxy vision OCR fallback for image captchas."""

from unittest.mock import AsyncMock

import httpx
import pytest

from job_ftch.infrastructure.bypass.captcha_models import (
    CaptchaFailureReason,
    CaptchaSolveResult,
)
from job_ftch.infrastructure.bypass.captcha_providers import (
    CapSolverProvider,
    CliproxyImageProvider,
)
from job_ftch.infrastructure.bypass.captcha_solver import (
    CAPTCHA_PROVIDER_ENV_KEYS,
    CaptchaSolverBypass,
    _provider_api_key,
)


def test_cliproxy_image_reuses_openai_compatible_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert CAPTCHA_PROVIDER_ENV_KEYS["cliproxy_image"] == "JOB_FTCH_OPENAI_API_KEY"
    assert "CAPTCHA_VISION_API_KEY" not in CAPTCHA_PROVIDER_ENV_KEYS.values()
    monkeypatch.setenv("JOB_FTCH_CAPTCHA_VISION_API_KEY", "stale-vision-key")
    assert _provider_api_key("cliproxy_image") != "stale-vision-key"
    assert _provider_api_key("cliproxy_image")


@pytest.mark.asyncio
async def test_cliproxy_image_reads_chat_completion_text(monkeypatch):
    calls = []

    def handle(request):
        import json

        payload = json.loads(request.content)
        calls.append(payload)
        assert request.url.path == "/v1/chat/completions"
        assert payload["model"] == "gemini-3-flash"
        assert payload["messages"][0]["content"][1]["image_url"]["url"] == (
            "data:image/png;base64,aW1hZ2U="
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " Q7kP \nignore"}}]},
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    page = AsyncMock()
    page.evaluate.return_value = "aW1hZ2U="
    result = await CliproxyImageProvider(
        "clip-key",
        base_url="http://cliproxy.test/v1",
        model="gemini-3-flash",
    ).solve(page, challenge_type="image", url="https://example.test/captcha")
    assert result.solved and result.tokens == {"captcha_token": "Q7kP"}
    assert calls and calls[0]["max_tokens"] == 32


@pytest.mark.asyncio
async def test_cliproxy_image_rejects_widget_challenges():
    result = await CliproxyImageProvider(
        "clip-key",
        base_url="http://cliproxy.test/v1",
        model="gemini-3-flash",
    ).solve(AsyncMock(), challenge_type="recaptcha", url="https://example.test")
    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.UNSUPPORTED_CHALLENGE


@pytest.mark.asyncio
async def test_cliproxy_image_skips_when_unconfigured():
    page = AsyncMock()
    page.evaluate.return_value = "aW1hZ2U="
    result = await CliproxyImageProvider(
        "",
        base_url="",
        model="gemini-3-flash",
    ).solve(page, challenge_type="image", url="https://example.test")
    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.MISSING_CREDENTIAL
    page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_image_chain_falls_back_to_cliproxy_after_capsolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def capsolver_rejected(self, page, *, challenge_type: str, url: str):
        del self, page, challenge_type, url
        return CaptchaSolveResult(
            solved=False,
            method="capsolver",
            failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
        )

    async def cliproxy_ok(self, page, *, challenge_type: str, url: str):
        del self, page, challenge_type, url
        return CaptchaSolveResult(
            solved=True,
            method="cliproxy_image",
            tokens={"captcha_token": "Q7kP"},
        )

    monkeypatch.setattr(CapSolverProvider, "solve", capsolver_rejected)
    monkeypatch.setattr(CliproxyImageProvider, "solve", cliproxy_ok)
    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-key")
    monkeypatch.setenv("JOB_FTCH_OPENAI_API_KEY", "clip-key")

    solver = CaptchaSolverBypass(
        enabled_providers=frozenset({"capsolver", "cliproxy_image"}),
        max_paid_attempts=1,
        min_provider_seconds=0,
    )
    solver._inject_token = AsyncMock(return_value=True)  # type: ignore[method-assign]
    solver._check_challenge_cleared = AsyncMock(return_value=True)  # type: ignore[method-assign]
    result = await solver.solve(
        AsyncMock(),
        challenge_type="image",
        url="https://example.test/captcha",
    )
    assert result.solved is True
    assert result.method == "cliproxy_image"


@pytest.mark.asyncio
async def test_image_chain_falls_back_when_capsolver_token_fails_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def capsolver_token(self, page, *, challenge_type: str, url: str):
        del self, page, challenge_type, url
        return CaptchaSolveResult(
            solved=True,
            method="capsolver",
            tokens={"captcha_token": "wrong"},
        )

    async def cliproxy_ok(self, page, *, challenge_type: str, url: str):
        del self, page, challenge_type, url
        return CaptchaSolveResult(
            solved=True,
            method="cliproxy_image",
            tokens={"captcha_token": "Q7kP"},
        )

    monkeypatch.setattr(CapSolverProvider, "solve", capsolver_token)
    monkeypatch.setattr(CliproxyImageProvider, "solve", cliproxy_ok)
    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-key")
    monkeypatch.setenv("JOB_FTCH_OPENAI_API_KEY", "clip-key")

    solver = CaptchaSolverBypass(
        enabled_providers=frozenset({"capsolver", "cliproxy_image"}),
        max_paid_attempts=1,
        min_provider_seconds=0,
        backoff_seconds=60,
    )
    solver._inject_token = AsyncMock(return_value=True)  # type: ignore[method-assign]
    solver._check_challenge_cleared = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]
    page = AsyncMock()
    result = await solver.solve(page, challenge_type="image", url="https://hh.ru/account/captcha")
    assert result.solved is True
    assert result.method == "cliproxy_image"
    assert solver._check_challenge_cleared.await_count == 2


@pytest.mark.asyncio
async def test_image_chain_backs_off_only_after_cliproxy_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def token_result(method: str):
        async def _solve(self, page, *, challenge_type: str, url: str):
            del self, page, challenge_type, url
            return CaptchaSolveResult(
                solved=True,
                method=method,
                tokens={"captcha_token": "nope"},
            )

        return _solve

    monkeypatch.setattr(CapSolverProvider, "solve", await token_result("capsolver"))
    monkeypatch.setattr(CliproxyImageProvider, "solve", await token_result("cliproxy_image"))
    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-key")
    monkeypatch.setenv("JOB_FTCH_OPENAI_API_KEY", "clip-key")

    solver = CaptchaSolverBypass(
        enabled_providers=frozenset({"capsolver", "cliproxy_image"}),
        max_paid_attempts=1,
        min_provider_seconds=0,
        backoff_seconds=60,
    )
    solver._inject_token = AsyncMock(return_value=True)  # type: ignore[method-assign]
    solver._check_challenge_cleared = AsyncMock(return_value=False)  # type: ignore[method-assign]
    page = AsyncMock()
    first = await solver.solve(page, challenge_type="image", url="https://hh.ru/account/captcha")
    assert first.solved is False
    assert first.failure_reason is CaptchaFailureReason.PROVIDER_REJECTED
    assert solver._check_challenge_cleared.await_count == 2
    second = await solver.solve(page, challenge_type="image", url="https://hh.ru/account/captcha")
    assert second.failure_reason is CaptchaFailureReason.BACKOFF_ACTIVE
