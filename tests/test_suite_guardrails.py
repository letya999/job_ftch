from __future__ import annotations

import socket

import pytest

from tests.conftest import UnexpectedNetworkAccess


def test_offline_suite_blocks_external_dns() -> None:
    with pytest.raises(UnexpectedNetworkAccess, match="external DNS lookup blocked"):
        socket.getaddrinfo("example.com", 443)


def test_offline_suite_blocks_direct_external_ip_connections() -> None:
    with (
        socket.socket() as client,
        pytest.raises(UnexpectedNetworkAccess, match="external socket connection blocked"),
    ):
        client.connect(("203.0.113.1", 443))


def test_offline_suite_allows_loopback_resolution() -> None:
    assert socket.getaddrinfo("localhost", 80)
