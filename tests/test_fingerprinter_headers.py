"""Tests for the fingerprint probe headers and SPA render-hint propagation.

Covers Phase SONNET-2: the probe must send a realistic Chrome UA/Accept
(instead of the new, instantly-blocked "SiteFingerprinter/1.0" UA), and SPA
classification must surface ``detected_config={"render": True}`` so the DOM
monitor knows to render JS instead of parsing static HTML.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from job_ftch.infrastructure.sources.monitor_detector import (
    _prefer_listing_monitor_for_filtered_url,
    detect_monitor_type,
    get_ordered_monitors,
)
from job_ftch.infrastructure.sources.site_fingerprinter import (
    PROBE_ACCEPT,
    PROBE_USER_AGENT,
    SiteClass,
    fingerprint,
)


def test_filtered_listing_prefers_dom_over_sitewide_sitemap() -> None:
    monitors = _prefer_listing_monitor_for_filtered_url(
        "https://example.com/vacancies?query=AI",
        ["sitemap", "dom", "api_sniffer"],
    )

    assert monitors == ["dom", "sitemap", "api_sniffer"]


@pytest.fixture(autouse=True)
def _disable_curl_stealth(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_value_error(name: str):
        raise ValueError(name)

    monkeypatch.setattr("job_ftch.application.registry.resolve_bypass", _raise_value_error)


def _mock_client(response=None, side_effect=None):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def test_probe_user_agent_is_chrome_like():
    assert "Chrome/" in PROBE_USER_AGENT
    assert "SiteFingerprinter" not in PROBE_USER_AGENT
    assert "Windows NT" in PROBE_USER_AGENT


def test_probe_accept_matches_real_chrome_fetch():
    assert PROBE_ACCEPT.startswith("text/html")
    assert "application/xhtml+xml" in PROBE_ACCEPT
    assert PROBE_ACCEPT.endswith("*/*;q=0.8")


@pytest.mark.asyncio
async def test_fingerprint_probe_sends_chrome_headers():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = "Just some plain text without any job signals. " * 200

    captured_headers: dict[str, str] = {}
    prebuilt_client = _mock_client(response=mock_response)

    def _capture_client(*args, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return prebuilt_client

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        side_effect=_capture_client,
    ):
        await fingerprint("https://example.com/jobs")

    assert captured_headers.get("User-Agent") == PROBE_USER_AGENT
    assert "Chrome/" in captured_headers.get("User-Agent", "")
    assert captured_headers.get("Accept") == PROBE_ACCEPT


@pytest.mark.asyncio
async def test_fingerprint_spa_via_hints_sets_render_config():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = (
        '<html><head></head><body><div id="__next">'
        '<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>'
        "</div></body></html>"
    )

    mock_client = _mock_client(response=mock_response)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/next-jobs")

    assert profile.site_class == SiteClass.SPA
    assert profile.detected_config == {"render": True}


@pytest.mark.asyncio
async def test_fingerprint_spa_via_short_body_sets_render_config():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><body><div id="root"></div></body></html>'

    mock_client = _mock_client(response=mock_response)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/spa-jobs")

    assert profile.site_class == SiteClass.SPA
    assert profile.detected_config == {"render": True}


@pytest.mark.asyncio
async def test_fingerprint_ssr_does_not_set_render_config():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<a href="/vacancy/123">Job</a>' * 5

    mock_client = _mock_client(response=mock_response)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.SSR
    assert profile.detected_config == {}


@pytest.mark.asyncio
async def test_fingerprint_blocked_still_returns_empty_config():
    mock_client = _mock_client(side_effect=httpx.ConnectError("Connection failed"))

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.BLOCKED
    assert profile.detected_config == {}
    assert profile.recommended_monitors == ["dom", "api_sniffer"]


@pytest.mark.asyncio
async def test_detect_monitor_type_propagates_render_hint():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><body><div id="root"></div></body></html>'

    mock_client = _mock_client(response=mock_response)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await detect_monitor_type("https://example.com/spa-jobs", client=None)

    assert result is not None
    monitor_name, detected_config = result
    assert monitor_name == "api_sniffer"
    assert detected_config == {"render": True}


@pytest.mark.asyncio
async def test_get_ordered_monitors_propagates_render_hint() -> None:
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><body><div id="root"></div></body></html>'

    mock_client = _mock_client(response=mock_response)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        monitors, detected_config, _canonical = await get_ordered_monitors(
            "https://example.com/spa-jobs",
            client=None,
        )

    assert monitors == ["api_sniffer", "dom"]
    assert detected_config == {"render": True}


@pytest.mark.asyncio
async def test_get_ordered_monitors_promotes_specific_monitor_from_can_handle() -> None:
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><body><div id="root"></div></body></html>'

    mock_client = _mock_client(response=mock_response)

    async def fake_can_handle(url: str, client: object) -> dict[str, str]:
        del url, client
        return {"token": "acme"}

    fake_entry = MagicMock()
    fake_entry.name = "ashby"
    fake_entry.can_handle = fake_can_handle

    with (
        patch(
            "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch(
            "job_ftch.infrastructure.sources.monitor_detector.get_all_monitor_entries",
            return_value=[fake_entry],
        ),
    ):
        monitors, detected_config, _canonical = await get_ordered_monitors(
            "https://example.com/spa-jobs",
            client=None,
        )

    assert monitors == ["ashby", "api_sniffer", "dom"]
    assert detected_config == {"token": "acme", "render": True}
