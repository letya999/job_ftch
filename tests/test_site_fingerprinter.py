from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
from job_ftch.infrastructure.sources.site_fingerprinter import SiteClass, SiteProfile, fingerprint


@pytest.fixture(autouse=True)
def _disable_curl_stealth(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_value_error(name: str):
        raise ValueError(name)

    monkeypatch.setattr("job_ftch.application.registry.resolve_bypass", _raise_value_error)


@pytest.mark.asyncio
async def test_fingerprint_ssr_via_vacancy_links():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<a href="/vacancy/123">Job</a>' * 5

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.SSR
    assert profile.recommended_monitors[0] == "dom"


@pytest.mark.asyncio
async def test_fingerprint_blocked_403():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 403
    mock_response.headers = {}
    mock_response.text = "Forbidden"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.BLOCKED
    assert "dom" in profile.recommended_monitors


@pytest.mark.asyncio
async def test_fingerprint_blocked_429():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 429
    mock_response.headers = {}
    mock_response.text = "Too Many Requests"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.BLOCKED
    assert "dom" in profile.recommended_monitors


@pytest.mark.asyncio
async def test_fingerprint_rss_content_type():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/rss+xml"}
    mock_response.text = "<rss><channel><item><title>Job</title></item></channel></rss>"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/feed")

    assert profile.site_class == SiteClass.RSS
    assert profile.recommended_monitors[0] == "rss_board"


@pytest.mark.asyncio
async def test_fingerprint_json_api():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.text = '[{"id": 1, "title": "Python Dev"}]'

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/api/jobs")

    assert profile.site_class == SiteClass.API_JSON
    assert profile.recommended_monitors[0] == "api_sniffer"


@pytest.mark.asyncio
async def test_fingerprint_spa_next_data():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><head></head><body><div id="__next"><script id="__NEXT_DATA__" type="application/json">{"props":{}}</script></div></body></html>'

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/next-jobs")

    assert profile.site_class == SiteClass.SPA
    assert profile.recommended_monitors[0] == "api_sniffer"


@pytest.mark.asyncio
async def test_fingerprint_spa_short_body():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<html><body><div id="root"></div></body></html>'

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/spa-jobs")

    assert profile.site_class == SiteClass.SPA


@pytest.mark.asyncio
async def test_fingerprint_connection_error():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.BLOCKED
    assert profile.recommended_monitors == ["dom", "api_sniffer"]


@pytest.mark.asyncio
async def test_fingerprint_timeout():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.BLOCKED


@pytest.mark.asyncio
async def test_fingerprint_unknown_sparse_html():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = "Just some random text without any links. " * 1000

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        profile = await fingerprint("https://example.com/jobs")

    assert profile.site_class == SiteClass.UNKNOWN
    assert "dom" in profile.recommended_monitors


@pytest.mark.asyncio
async def test_get_ordered_monitors_ssr():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<a href="/vacancy/123">Job</a>' * 5

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
        return_value=mock_client,
    ):
        monitors, _, _canonical = await get_ordered_monitors("https://example.com/jobs", None)

    assert monitors[0] == "dom"


@pytest.mark.asyncio
async def test_get_ordered_monitors_fallback_on_error():
    with patch(
        "job_ftch.infrastructure.sources.monitor_detector.fingerprint",
        side_effect=Exception("Fingerprint error"),
    ):
        monitors, _, _canonical = await get_ordered_monitors("https://example.com/jobs", None)

    assert monitors == ["dom", "api_sniffer"]


def test_site_profile_frozen_dataclass():
    p = SiteProfile(site_class=SiteClass.SSR, recommended_monitors=["dom"], detected_config={})
    with pytest.raises(FrozenInstanceError):
        p.site_class = SiteClass.SPA
