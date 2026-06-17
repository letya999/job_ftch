import socket
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from adapters.telegram_bot.handlers.pipeline import _host_resolves_to_blocked_ip, _url_is_alive


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

        with patch("aiohttp.ClientSession", return_value=mock_session):
            assert await _url_is_alive(url) is True
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

        with patch("aiohttp.ClientSession", return_value=mock_session):
            # We expect True because 301 < 400, even though allow_redirects=False
            assert await _url_is_alive(url) is True
            mock_session.head.assert_called_once_with(
                url, timeout=pytest.any_instance_of(aiohttp.ClientTimeout), allow_redirects=False
            )


# Helper for fuzzy matching in assert_called_once_with
class AnyInstanceOf:
    def __init__(self, cls):
        self.cls = cls

    def __eq__(self, other):
        return isinstance(other, self.cls)


pytest.any_instance_of = AnyInstanceOf
