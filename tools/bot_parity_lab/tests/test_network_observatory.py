from __future__ import annotations

import base64

import pytest

from paritylab.network_observatory import (
    HTTP2_PREFACE,
    ProtocolObservationError,
    fingerprint_http2,
    fingerprint_http3_streams,
    fingerprint_dns,
    fingerprint_quic,
    fingerprint_tls_lifecycle,
    fingerprint_tcpip,
    parse_http2_frames,
)


def _frame(frame_type: int, flags: int, stream_id: int, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(3, "big")
        + bytes((frame_type, flags))
        + stream_id.to_bytes(4, "big")
        + payload
    )


def test_http2_settings_window_and_sequence_fingerprint() -> None:
    settings = b"\x00\x01" + (65536).to_bytes(4, "big") + b"\x00\x04" + (6291456).to_bytes(4, "big")
    wire = (
        HTTP2_PREFACE + _frame(4, 0, 0, settings) + _frame(8, 0, 0, (15663105).to_bytes(4, "big"))
    )
    frames = parse_http2_frames(wire)
    result = fingerprint_http2(
        frames, pseudo_header_order=(":method", ":authority", ":scheme", ":path")
    )
    assert result.settings == ((1, 65536), (4, 6291456))
    assert result.connection_window_updates == (15663105,)
    assert result.frame_sequence == ("4:0:0:12", "8:0:0:4")
    assert len(result.sha256) == 64


def test_http2_parser_rejects_truncated_frames() -> None:
    with pytest.raises(ProtocolObservationError, match="truncated"):
        parse_http2_frames(HTTP2_PREFACE + b"\x00\x00\x04\x04")


def test_quic_fingerprint_is_order_independent_for_transport_parameters() -> None:
    first = fingerprint_quic(
        {
            "version": "1",
            "alpn": "h3",
            "transport_parameters": {"max_idle_timeout": 30000, "initial_max_data": 1000000},
            "session_resumed": False,
            "early_data_accepted": False,
        }
    )
    second = fingerprint_quic(
        {
            "alpn": "h3",
            "version": "1",
            "transport_parameters": {"initial_max_data": 1000000, "max_idle_timeout": 30000},
            "early_data_accepted": False,
            "session_resumed": False,
        }
    )
    assert first.sha256 == second.sha256
    assert first.transport_parameters[0][0] == "initial_max_data"


def test_quic_fingerprint_rejects_ambiguous_flags() -> None:
    with pytest.raises(ProtocolObservationError, match="boolean"):
        fingerprint_quic({"transport_parameters": {}, "session_resumed": "false"})


def test_tls_lifecycle_fingerprint_captures_resumption_and_tickets() -> None:
    result = fingerprint_tls_lifecycle(
        {
            "negotiated_version": "TLSv1.3",
            "alpn": "h2",
            "resumed": True,
            "early_data_accepted": False,
            "new_session_tickets": 2,
            "key_updates": 1,
            "handshake_duration_ms": 12.75,
        }
    )
    assert result.resumed is True
    assert result.new_session_tickets == 2
    assert len(result.sha256) == 64


def test_tls_lifecycle_rejects_invalid_ticket_count() -> None:
    with pytest.raises(ProtocolObservationError, match="new_session_tickets"):
        fingerprint_tls_lifecycle({"new_session_tickets": -1})


def test_http3_stream_fingerprint_parses_settings_and_qpack_shape() -> None:
    control = bytes((0, 4, 3, 1, 0x50, 0))
    qpack_encoder = bytes((2, 0x3F, 0x01, 0x80))
    result = fingerprint_http3_streams(
        [
            {"data_base64": base64.b64encode(control).decode("ascii")},
            {"data_base64": base64.b64encode(qpack_encoder).decode("ascii")},
        ]
    )
    assert result.settings == ((1, 4096),)
    assert result.stream_types == (0, 2)
    assert result.frame_sequence == ("0:4:3",)
    assert result.qpack_stream_shapes[0].startswith("2:3:")


def test_http3_stream_parser_rejects_truncated_frame() -> None:
    with pytest.raises(ProtocolObservationError, match="truncated HTTP/3 frame"):
        fingerprint_http3_streams(
            [{"stream_type": 0, "data_base64": base64.b64encode(bytes((4, 8, 1))).decode()}]
        )


def test_dns_fingerprint_normalizes_privacy_safe_aggregates() -> None:
    result = fingerprint_dns(
        {
            "transport": "doh",
            "query_count": 3,
            "qtypes": {"AAAA": 1, "A": 2},
            "rcodes": {"NOERROR": 3},
            "cache_hit_count": 1,
            "median_duration_ms": 12.5,
            "encrypted": True,
            "query_shape_hashes": ["abcdef0123456789"],
        }
    )
    assert result.qtypes == (("A", 2), ("AAAA", 1))
    assert result.encrypted is True
    assert len(result.sha256) == 64


def test_dns_fingerprint_rejects_raw_query_labels() -> None:
    with pytest.raises(ProtocolObservationError, match="pre-redacted hashes"):
        fingerprint_dns({"query_shape_hashes": ["example.com"]})


def test_tcpip_fingerprint_normalizes_syn_and_pacing_shape() -> None:
    result = fingerprint_tcpip(
        {
            "ip_version": 4,
            "initial_ttl": 128,
            "observed_ttl": 126,
            "tcp_window": 64240,
            "window_scale": 8,
            "mss": 1460,
            "option_order": ["mss", "sack", "timestamps", "nop", "wscale"],
            "sack_permitted": True,
            "timestamps": True,
            "ecn": False,
            "syn_retransmissions": 0,
            "pacing_ms": [0.0, 3.25, 8.5],
        }
    )
    assert result.hop_estimate == 2
    assert result.mss == 1460
    assert len(result.sha256) == 64


def test_tcpip_fingerprint_rejects_impossible_ttl() -> None:
    with pytest.raises(ProtocolObservationError, match="observed_ttl exceeds"):
        fingerprint_tcpip(
            {
                "ip_version": 4,
                "initial_ttl": 64,
                "observed_ttl": 65,
                "tcp_window": 1,
                "sack_permitted": False,
                "timestamps": False,
                "ecn": False,
            }
        )
