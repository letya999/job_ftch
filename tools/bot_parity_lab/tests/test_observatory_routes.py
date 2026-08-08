from __future__ import annotations

import asyncio
import base64

from starlette.testclient import TestClient

from paritylab.app import create_app
from paritylab.config import LabConfig
from paritylab.network_observatory import HTTP2_PREFACE
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring import score_session
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry


def _frame(frame_type: int, flags: int, stream_id: int, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(3, "big")
        + bytes((frame_type, flags))
        + stream_id.to_bytes(4, "big")
        + payload
    )


def test_observatory_ingests_http2_and_quic_evidence(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    store = ArtifactStore(config.artifacts_dir)
    app = create_app(config, store=store, registry=TLSConnectionRegistry())
    settings = b"\x00\x01" + (65536).to_bytes(4, "big")
    wire = HTTP2_PREFACE + _frame(4, 0, 0, settings)
    with TestClient(app) as client:
        http2 = client.post(
            "/api/observatory/http2?sid=transport-session",
            json={
                "source": "tshark-keylog",
                "wire_base64": base64.b64encode(wire).decode("ascii"),
                "pseudo_header_order": [":method", ":authority", ":scheme", ":path"],
            },
        )
        assert http2.status_code == 200
        assert http2.json()["observation"]["evidence"]["settings"] == [[1, 65536]]
        quic = client.post(
            "/api/observatory/quic?sid=transport-session",
            json={
                "source": "aioquic-events",
                "metadata": {
                    "version": "1",
                    "alpn": "hq-interop",
                    "transport_parameters": {"initial_max_data": 1_000_000},
                    "session_resumed": False,
                },
            },
        )
        assert quic.status_code == 200
        tls = client.post(
            "/api/observatory/tls?sid=transport-session",
            json={
                "source": "tshark-keylog",
                "metadata": {
                    "negotiated_version": "TLSv1.3",
                    "alpn": "h2",
                    "resumed": True,
                    "early_data_accepted": False,
                    "new_session_tickets": 2,
                    "key_updates": 0,
                    "handshake_duration_ms": 14.25,
                },
            },
        )
        assert tls.status_code == 200
        http3 = client.post(
            "/api/observatory/http3?sid=transport-session",
            json={
                "source": "aioquic-decrypted-streams",
                "streams": [
                    {"data_base64": base64.b64encode(bytes((0, 4, 3, 1, 0x50, 0))).decode("ascii")}
                ],
            },
        )
        assert http3.status_code == 200
        dns = client.post(
            "/api/observatory/dns?sid=transport-session",
            json={
                "source": "pcap-redacted",
                "metadata": {
                    "transport": "doh",
                    "query_count": 2,
                    "qtypes": {"A": 1, "AAAA": 1},
                    "rcodes": {"NOERROR": 2},
                    "cache_hit_count": 0,
                    "median_duration_ms": 8.5,
                    "encrypted": True,
                    "query_shape_hashes": ["0123456789abcdef"],
                },
            },
        )
        assert dns.status_code == 200
        tcpip = client.post(
            "/api/observatory/tcpip?sid=transport-session",
            json={
                "source": "tshark-syn",
                "metadata": {
                    "ip_version": 4,
                    "initial_ttl": 128,
                    "observed_ttl": 128,
                    "tcp_window": 64240,
                    "window_scale": 8,
                    "mss": 1460,
                    "option_order": ["mss", "sack", "timestamps", "nop", "wscale"],
                    "sack_permitted": True,
                    "timestamps": True,
                    "ecn": False,
                    "syn_retransmissions": 0,
                    "pacing_ms": [0.0, 4.5],
                },
            },
        )
        assert tcpip.status_code == 200

    state = asyncio.run(store.get("transport-session"))
    assert state is not None
    assert [item.protocol for item in state.protocol_observations] == [
        "http2",
        "quic",
        "tls",
        "http3",
        "dns",
        "tcpip",
    ]
    findings, _ = score_session(state, reputation=OfflineIPReputation(config.ip_reputation_file))
    codes = {item.code for item in findings}
    assert "NET_PROTOCOL_OBSERVATORY_CAPTURED" in codes
    assert "NET_QUIC_ALPN_CONFLICT" in codes
    assert "TLS_LIFECYCLE_OBSERVATORY_CAPTURED" in codes


def test_observatory_rejects_malformed_http2_wire(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    app = create_app(
        config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry()
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/observatory/http2?sid=bad-transport",
            json={"wire_base64": base64.b64encode(b"not-http2").decode("ascii")},
        )
    assert response.status_code == 400
    assert "preface" in response.json()["error"]
