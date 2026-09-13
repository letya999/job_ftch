"""Offline checks: detection and operator-only continuation, no solver APIs."""

from unittest.mock import AsyncMock

import pytest

from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass
from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge
from job_ftch.infrastructure.bypass.failure_signal import FailureKind, _detect_captcha_type

IMAGE = '<form><img src="/captcha/image"><input name="captchaText"></form>'
CHECKBOX = '<iframe src="https://recaptcha.net/recaptcha/api2/anchor?k=public"></iframe>'
TARGET = "https://example.test/vacancy/123"


@pytest.mark.parametrize(("html", "kind"), [(IMAGE, "image"), (CHECKBOX, "recaptcha")])
def test_active_captcha_overrides_long_page_content(html, kind):
    detection = classify_challenge(
        surface="test", status_code=200, body=html + "Instructions. " * 100
    )
    assert detection.kind == FailureKind.CAPTCHA
    assert detection.challenge_type == kind


def test_explicit_render_is_not_v3():
    assert (
        _detect_captcha_type(
            '<script src="https://recaptcha.net/recaptcha/api.js?render=explicit&hl=ru"></script>'
        )
        == "recaptcha"
    )


def test_normal_vacancy_mentions_do_not_trigger_captcha():
    detection = classify_challenge(
        surface="test",
        status_code=200,
        body="<h1>Engineer</h1>"
        + "Work on captcha recognition. " * 30
        + '<script src="https://recaptcha.net/recaptcha/api.js?render=explicit"></script>',
    )
    assert not detection.detected
    restricted = classify_challenge(surface="test", status_code=403, body="Unavailable vacancy")
    assert restricted.challenge_type is None


@pytest.mark.asyncio
async def test_image_and_captcha_word_are_not_a_captcha_form():
    page = OperatorPage(html='<img src="/company.png">' + "Develop captcha tools. " * 30)
    assert await CaptchaSolverBypass()._detect_challenge(page) is None


class OperatorPage:
    url = "https://example.test/account/captcha"

    def __init__(self, completed=False, html=IMAGE):
        self.completed = completed
        self.html = html
        self.content = AsyncMock(side_effect=self.read)
        self.evaluate = AsyncMock(
            side_effect=lambda js: "complete" if js == "document.readyState" else True
        )
        self.click = AsyncMock()
        self.fill = AsyncMock()

    async def read(self):
        if self.completed:
            self.url = TARGET
            return "<h1>Engineer</h1><p>Build systems</p>"
        return self.html


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [IMAGE, CHECKBOX])
async def test_manual_mode_pauses_without_clicks_or_external_calls(html):
    page = OperatorPage(html=html)
    solver = CaptchaSolverBypass(
        provider="manual_required", wait_seconds=0.001, authorized_domains=frozenset()
    )
    solver._solve_external_api = AsyncMock(side_effect=AssertionError("external API forbidden"))
    result = await solver.solve(page, challenge_type=_detect_captcha_type(html), url=TARGET)
    assert not result.solved
    assert result.result_kind == "manual_required"
    page.click.assert_not_called()
    page.fill.assert_not_called()
    solver._solve_external_api.assert_not_called()


@pytest.mark.asyncio
async def test_manual_completion_continues_in_same_session():
    page = OperatorPage(completed=True)
    solver = CaptchaSolverBypass(provider="manual_required", authorized_domains=frozenset())
    result = await solver.solve(page, challenge_type="image", url=TARGET)
    assert result.solved
    assert not result.tokens and not result.cookies
    page.click.assert_not_called()
    page.fill.assert_not_called()


@pytest.mark.asyncio
async def test_manual_mode_never_uses_configured_paid_route():
    page = OperatorPage()
    solver = CaptchaSolverBypass(
        provider="manual_required",
        wait_seconds=0.001,
        provider_routes={"image": ("capsolver",)},
        max_attempts=1,
    )
    solver._solve_external_api = AsyncMock(side_effect=AssertionError("no external calls"))
    for _ in range(3):
        result = await solver.solve(page, challenge_type="image", url=TARGET)
        assert result.result_kind == "manual_required"
    solver._solve_external_api.assert_not_called()


@pytest.mark.asyncio
async def test_manual_mode_requires_successful_document_response():
    page = OperatorPage(completed=True)
    page.evaluate = AsyncMock(
        side_effect=lambda js: "complete" if js == "document.readyState" else False
    )
    solver = CaptchaSolverBypass(provider="manual_required", wait_seconds=0.001)
    result = await solver.solve(page, challenge_type="image", url=TARGET)
    assert not result.solved
