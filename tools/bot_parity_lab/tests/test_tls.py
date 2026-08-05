from __future__ import annotations

import struct

from paritylab.tls import ja3_fingerprint, ja4_fingerprint, parse_tls_client_hello


def _ext(extension_id: int, payload: bytes) -> bytes:
    return struct.pack("!HH", extension_id, len(payload)) + payload


def _client_hello() -> bytes:
    server_name = b"localhost"
    sni_names = b"\x00" + struct.pack("!H", len(server_name)) + server_name
    sni = struct.pack("!H", len(sni_names)) + sni_names
    groups = struct.pack("!H", 4) + struct.pack("!HH", 29, 23)
    points = b"\x01\x00"
    sigs = struct.pack("!H", 4) + struct.pack("!HH", 0x0804, 0x0403)
    alpn_list = b"\x02h2\x08http/1.1"
    alpn = struct.pack("!H", len(alpn_list)) + alpn_list
    versions = b"\x04" + struct.pack("!HH", 0x0304, 0x0303)
    extensions = b"".join(
        [
            _ext(0, sni),
            _ext(10, groups),
            _ext(11, points),
            _ext(13, sigs),
            _ext(16, alpn),
            _ext(43, versions),
        ]
    )
    body = b"".join(
        [
            struct.pack("!H", 0x0303),
            bytes(range(32)),
            b"\x00",
            struct.pack("!H", 6),
            struct.pack("!HHH", 0x1301, 0x1302, 0x1303),
            b"\x01\x00",
            struct.pack("!H", len(extensions)),
            extensions,
        ]
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def test_client_hello_and_fingerprints_are_deterministic() -> None:
    hello = parse_tls_client_hello(_client_hello())
    assert hello.server_name == "localhost"
    assert hello.alpn_protocols == ("h2", "http/1.1")
    assert hello.supported_versions == (0x0304, 0x0303)
    assert hello.cipher_suites == (0x1301, 0x1302, 0x1303)

    ja3_raw, ja3 = ja3_fingerprint(hello)
    ja4_raw, ja4 = ja4_fingerprint(hello)
    assert ja3_raw.startswith("771,4865-4866-4867,")
    assert len(ja3) == 32
    assert ja4.startswith("t13d0306h2_")
    assert len(ja4.split("_")) == 3
    assert "1301,1302,1303" in ja4_raw
