from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
from job_ftch.infrastructure.sources.site_fingerprinter import (
    SiteClass,
    fingerprint,
    known_monitor_from_url,
)


def test_known_monitor_from_url():
    assert known_monitor_from_url("https://boards.greenhouse.io/openai") == "greenhouse"
    assert known_monitor_from_url("https://jobs.lever.co/stripe") == "lever_board"
    assert known_monitor_from_url("https://stripe.wd1.myworkdayjobs.com/Careers") == "workday"
    assert known_monitor_from_url("https://google.com/jobs") is None


@pytest.mark.asyncio
async def test_fingerprint_shortcircuit_no_network():
    class DummyClient:
        def __init__(self) -> None:
            self.get = AsyncMock(side_effect=RuntimeError("Should not be called!"))

    client = DummyClient()
    profile = await fingerprint("https://boards.greenhouse.io/openai", client)

    assert profile.recommended_monitors == ["greenhouse"]
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_ordered_monitors_shortcircuit_no_network():
    class DummyClient:
        def __init__(self) -> None:
            self.get = AsyncMock(side_effect=RuntimeError("Should not be called!"))

    client = DummyClient()
    monitors, config, _canonical = await get_ordered_monitors(
        "https://boards.greenhouse.io/openai", client
    )

    assert monitors == ["greenhouse"]
    assert config == {}
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_captcha_detection(monkeypatch: pytest.MonkeyPatch):
    class DummyResponse:
        status_code = 403
        text = "<html><body>datadome challenge here</body></html>"
        headers = {"content-type": "text/html"}
        url = httpx.URL("https://example.com")

    class DummyClient:
        async def get(self, *args, **kwargs):
            return DummyResponse()

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_bypass",
        lambda name: (_ for _ in ()).throw(ValueError(name)),
    )

    client = DummyClient()
    profile = await fingerprint("https://example.com/blocked", client)

    assert profile.site_class == SiteClass.BLOCKED
    assert profile.detected_config == {"challenge": True, "render": True}


@pytest.mark.asyncio
async def test_fingerprint_ignores_embedded_recaptcha_on_substantial_page(
    monkeypatch: pytest.MonkeyPatch,
):
    class DummyResponse:
        status_code = 200
        text = "<html><body>" + ("Open job role in Tallinn. " * 30) + "g-recaptcha</body></html>"
        headers = {"content-type": "text/html"}
        url = httpx.URL("https://example.com/careers")

    class DummyClient:
        async def get(self, *args, **kwargs):
            return DummyResponse()

    monkeypatch.setattr(
        "job_ftch.application.registry.resolve_bypass",
        lambda name: (_ for _ in ()).throw(ValueError(name)),
    )

    profile = await fingerprint("https://example.com/careers", DummyClient())

    assert profile.site_class != SiteClass.BLOCKED
    assert profile.detected_config.get("challenge") is None
