import socket
from unittest.mock import ANY, MagicMock, patch

import aiohttp
import pytest

from job_ftch.adapters.telegram_bot.formatter import resolve_job_url
from job_ftch.adapters.telegram_bot.handlers.pipeline import (
    _host_resolves_to_blocked_ip,
    _PinnedHostResolver,
    _url_is_alive,
)


def test_host_resolves_to_blocked_ip():
    # Loopback
    assert _host_resolves_to_blocked_ip("127.0.0.1") is True
    assert (
        _host_resolves_to_blocked_ip("localhost") is True
    )  # Assuming localhost resolves to 127.0.0.1/::1

    # Private RFC1918
    assert _host_resolves_to_blocked_ip("10.0.0.1") is True
    assert _host_resolves_to_blocked_ip("172.16.0.1") is True
    assert _host_resolves_to_blocked_ip("192.168.1.1") is True

    # Link-local / Cloud Metadata
    assert _host_resolves_to_blocked_ip("169.254.169.254") is True

    # None/Empty
    assert _host_resolves_to_blocked_ip(None) is True
    assert _host_resolves_to_blocked_ip("") is True

    # Public IP (example.com)
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ]
        assert _host_resolves_to_blocked_ip("example.com") is False

    # Unresolvable
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        mock_getaddrinfo.side_effect = socket.gaierror
        assert _host_resolves_to_blocked_ip("nonexistent.local") is True


@pytest.mark.asyncio
async def test_url_is_alive_telegram_passthrough():
    assert await _url_is_alive("https://t.me/job_channel") is True
    assert await _url_is_alive("http://telegram.me/foo") is True


@pytest.mark.asyncio
async def test_url_is_alive_telegram_lookalike_does_not_bypass_ssrf_guard():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("aiohttp.ClientSession") as mock_session:
            assert await _url_is_alive("https://evil-t.me.example/job") is False
            mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_url_is_alive_scheme_guard():
    assert await _url_is_alive("ftp://example.com/file") is False
    assert await _url_is_alive("file:///etc/passwd") is False


@pytest.mark.asyncio
async def test_url_is_alive_ssrf_blocked():
    # 1. Blocked via DNS resolution to private IP
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
        # Should return False without creating a ClientSession
        with patch("aiohttp.ClientSession") as mock_session:
            assert await _url_is_alive("http://internal-service/api") is False
            mock_session.assert_not_called()

    # 2. Blocked via literal private IP
    with patch("aiohttp.ClientSession") as mock_session:
        assert await _url_is_alive("http://127.0.0.1:8080") is False
        mock_session.assert_not_called()

    # 3. Mixed public/private answers are blocked as a set.
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]
        with patch("aiohttp.ClientSession") as mock_session:
            assert await _url_is_alive("http://mixed.example/api") is False
            mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_url_is_alive_happy_path():
    url = "https://example.com/job"
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        # Mock aiohttp response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp

        mock_session = MagicMock()
        mock_session.head.return_value = mock_resp
        mock_session.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_session) as client_session:
            assert await _url_is_alive(url) is True
            client_session.assert_called_once_with(connector=ANY)
            mock_session.head.assert_called_once_with(
                url, timeout=pytest.any_instance_of(aiohttp.ClientTimeout), allow_redirects=False
            )


@pytest.mark.asyncio
async def test_url_is_alive_redirect_treated_as_alive():
    url = "http://google.com"  # will redirect
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.180.142", 0))
        ]

        mock_resp = MagicMock()
        mock_resp.status = 301  # Moved Permanently
        mock_resp.__aenter__.return_value = mock_resp

        mock_session = MagicMock()
        mock_session.head.return_value = mock_resp
        mock_session.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_session) as client_session:
            # We expect True because 301 < 400, even though allow_redirects=False
            assert await _url_is_alive(url) is True
            client_session.assert_called_once_with(connector=ANY)
            mock_session.head.assert_called_once_with(
                url, timeout=pytest.any_instance_of(aiohttp.ClientTimeout), allow_redirects=False
            )


@pytest.mark.asyncio
async def test_url_liveness_does_not_hide_accepted_job_on_head_block_or_timeout():
    url = "https://example.com/job"
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        forbidden = MagicMock()
        forbidden.status = 403
        forbidden.__aenter__.return_value = forbidden
        session = MagicMock()
        session.head.return_value = forbidden
        session.__aenter__.return_value = session
        with patch("aiohttp.ClientSession", return_value=session):
            assert await _url_is_alive(url) is True

        with patch("aiohttp.ClientSession", side_effect=TimeoutError):
            assert await _url_is_alive(url) is True


@pytest.mark.asyncio
async def test_url_liveness_uses_pinned_resolver() -> None:
    url = "https://example.com/job"
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        response = MagicMock()
        response.status = 200
        response.__aenter__.return_value = response
        session = MagicMock()
        session.head.return_value = response
        session.__aenter__.return_value = session

        with patch("aiohttp.ClientSession", return_value=session) as client_session:
            assert await _url_is_alive(url) is True

    connector = client_session.call_args.kwargs["connector"]
    resolver = connector._resolver
    assert isinstance(resolver, _PinnedHostResolver)
    pinned = await resolver.resolve("example.com", 443)
    assert [entry["host"] for entry in pinned] == ["93.184.216.34"]
    mock_dns.assert_called_once()
    await connector.close()


# Helper for fuzzy matching in assert_called_once_with
class AnyInstanceOf:
    def __init__(self, cls):
        self.cls = cls

    def __eq__(self, other):
        return isinstance(other, self.cls)


pytest.any_instance_of = AnyInstanceOf


def test_resolve_job_url_prefers_canonical_but_falls_back_to_urls():
    canonical_job = MagicMock(canonical_url="https://example.com/1", urls=["https://example.com/x"])
    fallback_job = MagicMock(canonical_url=None, urls=["https://example.com/fallback"])
    empty_job = MagicMock(canonical_url=None, urls=[])

    assert resolve_job_url(canonical_job) == "https://example.com/1"
    assert resolve_job_url(fallback_job) == "https://example.com/fallback"
    assert resolve_job_url(empty_job) is None
