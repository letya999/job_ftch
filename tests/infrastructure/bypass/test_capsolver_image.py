"""Offline image recognition and post-submission verification checks."""

from unittest.mock import AsyncMock

import httpx
import pytest

from job_ftch.infrastructure.bypass.captcha_providers import CapMonsterProvider, CapSolverProvider
from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass
from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge


def test_fingerprint_script_is_not_a_block_page():
    html = "<script>const detected = navigator.webdriver;</script><h1>Engineer</h1>"
    detection = classify_challenge(surface="test", status_code=200, body=html + "Job duties. " * 30)
    assert not detection.detected
    blocked = classify_challenge(surface="test", status_code=200, body="Automation detected")
    assert blocked.detected


@pytest.mark.asyncio
async def test_image_task_uses_synchronous_text_response(monkeypatch):
    calls = []

    def handle(request):
        import json

        calls.append(json.loads(request.content))
        assert request.url.path == "/createTask"
        return httpx.Response(
            200,
            json={
                "errorId": 0,
                "status": "ready",
                "taskId": "image-task",
                "solution": {"text": " abc123 "},
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    page = AsyncMock()
    page.evaluate.return_value = "aW1hZ2U="
    result = await CapSolverProvider("offline-key").solve(
        page,
        challenge_type="image",
        url="https://example.test/captcha",
    )
    assert result.solved and result.tokens == {"captcha_token": "abc123"}
    assert result.provider_task_id == "image-task"
    assert calls[0]["task"] == {
        "type": "ImageToTextTask",
        "module": "common",
        "body": "aW1hZ2U=",
        "websiteURL": "https://example.test/captcha",
    }


@pytest.mark.asyncio
async def test_image_answer_uses_native_browser_input():
    page = AsyncMock()
    page.evaluate.return_value = True
    assert await CaptchaSolverBypass()._inject_token(page, "image", "abc123")
    page.fill.assert_awaited_once_with(
        'input[name="captchaText"]:visible', "abc123", timeout=10_000
    )
    page.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_clearance_requires_captcha_to_disappear(monkeypatch):
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.captcha_solver.sleep_with_source_deadline",
        AsyncMock(),
    )
    page = AsyncMock()
    page.content.return_value = '<form><img src="/captcha/image"><input name="captchaText"></form>'
    page.evaluate.side_effect = lambda js: (
        "complete" if js == "document.readyState" else "querySelectorAll('form')" not in js
    )
    solver = CaptchaSolverBypass()
    assert solver._provider_chain_for("image") == ("capsolver", "cliproxy_image", "observe")
    assert not await solver._check_challenge_cleared(page, "image")
    page.content.return_value = "<h1>Vacancies</h1><p>" + "Engineer role. " * 30 + "</p>"
    assert await solver._check_challenge_cleared(page, "image")


@pytest.mark.asyncio
@pytest.mark.parametrize("immediate", [True, False])
async def test_capmonster_image_module_and_polling(monkeypatch, immediate):
    import json

    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if request.url.path == "/createTask" and not immediate:
            return httpx.Response(200, json={"errorId": 0, "taskId": 123})
        return httpx.Response(
            200,
            json={
                "errorId": 0,
                "status": "ready",
                "taskId": 123,
                "solution": {"text": " soaps totemists "},
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.captcha_providers.sleep_with_source_deadline",
        AsyncMock(),
    )
    page = AsyncMock()
    page.evaluate.return_value = "aW1hZ2U="
    page._captcha_image_module = "yandexwavelatin"
    result = await CapMonsterProvider("offline-key").solve(
        page,
        challenge_type="image",
        url="https://example.test/captcha",
    )
    assert result.solved and result.tokens == {"captcha_token": "soaps totemists"}
    assert calls[0]["task"] == {
        "type": "ImageToTextTask",
        "body": "aW1hZ2U=",
        "capMonsterModule": "yandexwavelatin",
    }
    assert len(calls) == (1 if immediate else 2)
    assert result.provider_task_id == "123"


@pytest.mark.asyncio
async def test_capmonster_empty_image_never_calls_api(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(httpx, "AsyncClient", client)
    page = AsyncMock()
    page.evaluate.return_value = ""
    result = await CapMonsterProvider("offline-key").solve(page, challenge_type="image", url="")
    assert not result.solved
    client.assert_not_called()
