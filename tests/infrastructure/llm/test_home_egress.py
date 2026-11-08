"""Home proxy admits only an online, approved and pinned exit node."""

from __future__ import annotations

import asyncio

import pytest

from job_ftch.infrastructure.llm.home_egress import HomeEgress, approved_exit


def status(**changes):
    peer = {
        "ID": "node-a",
        "HostName": "android-home",
        "Online": True,
        "ExitNodeOption": True,
        "TailscaleIPs": ["100.64.0.9"],
    }
    peer.update(changes)
    return {"BackendState": "Running", "Peer": {"key": peer}}


def test_exit_requires_name_online_approval_and_pinned_identity():
    assert approved_exit(status(), "android-home", "node-a")["ID"] == "node-a"
    assert approved_exit(status(), "another-phone", "node-a") is None
    assert approved_exit(status(), "android-home", "node-b") is None
    assert approved_exit(status(Online=False), "android-home") is None
    assert approved_exit(status(ExitNodeOption=False), "android-home") is None
    assert approved_exit({"BackendState": "NeedsLogin"}, "android-home") is None
    duplicate = status()
    duplicate["Peer"]["other"] = dict(duplicate["Peer"]["key"], ID="node-b")
    assert approved_exit(duplicate, "android-home") is None


def test_admin_machine_rename_uses_dns_name_and_keeps_node_pin():
    renamed = status(HostName="Pixel-device", DNSName="android-home.tailnet.ts.net.")
    assert approved_exit(renamed, "android-home", "node-a")["ID"] == "node-a"
    assert approved_exit(renamed, "android-home", "node-b") is None


@pytest.mark.asyncio
async def test_proxy_returns_503_before_phone_is_ready():
    egress = HomeEgress()
    server = await asyncio.start_server(egress.relay, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        response = await asyncio.wait_for(reader.read(), 1)
        assert response.startswith(b"HTTP/1.1 503")
        assert not egress.clients
        writer.close()
        await writer.wait_closed()
