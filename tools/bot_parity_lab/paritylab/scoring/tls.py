from __future__ import annotations

import statistics
from itertools import pairwise

from paritylab.models import Finding, JsonValue, SessionState, SignalClass, TLSFingerprint
from paritylab.scoring.common import _finding

def _tls_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    request_connection_ids = {
        request.connection_id for request in session.requests if request.connection_id
    }
    fingerprints = [
        item
        for item in session.tls_fingerprints
        if item.parse_error is None
        and (not request_connection_ids or item.connection_id in request_connection_ids)
    ]
    if not fingerprints:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TLS_CLIENT_HELLO_UNAVAILABLE",
                "TLS ClientHello fingerprint unavailable",
                "No parseable ClientHello was correlated with the session. HTTP/3-only sessions may require QUIC capture extensions.",
            )
        )
        return findings

    ja4_values = {item.ja4 for item in fingerprints if item.ja4}
    if len(ja4_values) > 1:
        persona_shapes = {_tls_persona_shape(item) for item in fingerprints}
        if len(persona_shapes) == 1:
            findings.append(
                _finding(
                    SignalClass.INFO,
                    "TLS_LIFECYCLE_VARIANT",
                    "TLS lifecycle changed JA4 extension shape",
                    "JA4 values differ, but ciphers, groups, signatures, versions and ALPN remain one transport persona; resumption extensions commonly cause this.",
                    evidence={"ja4_values": sorted(value for value in ja4_values if value)},
                )
            )
        else:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "TLS_FINGERPRINT_DRIFT",
                    "TLS transport persona changed within one session",
                    "Multiple JA4 values also disagree on ciphers, groups, signatures, versions or ALPN.",
                    evidence={"ja4_values": sorted(value for value in ja4_values if value)},
                )
            )

    lifecycle: list[dict[str, JsonValue]] = []
    fingerprints_by_connection = {item.connection_id: item for item in fingerprints}
    for connection_id in sorted(request_connection_ids):
        requests = sorted(
            (item for item in session.requests if item.connection_id == connection_id),
            key=lambda item: item.monotonic_ns,
        )
        fingerprint = fingerprints_by_connection.get(connection_id)
        if not requests or fingerprint is None:
            continue
        gaps = [
            (current.monotonic_ns - previous.monotonic_ns) / 1_000_000
            for previous, current in pairwise(requests)
        ]
        lifecycle.append(
            {
                "connection_id": connection_id,
                "request_count": len(requests),
                "http_versions": sorted({item.http_version for item in requests}),
                "span_ms": round(
                    (requests[-1].monotonic_ns - requests[0].monotonic_ns) / 1_000_000, 3
                ),
                "median_gap_ms": round(statistics.median(gaps), 3) if gaps else None,
                "psk_offered": 41 in fingerprint.extension_ids,
                "early_data_offered": 42 in fingerprint.extension_ids,
                "alpn_offered": list(fingerprint.alpn_protocols),
                "ja4": fingerprint.ja4,
            }
        )
        if 42 in fingerprint.extension_ids and 41 not in fingerprint.extension_ids:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "TLS_EARLY_DATA_WITHOUT_PSK",
                    "TLS early-data extension lacks PSK",
                    "ClientHello offers early_data without pre_shared_key, an incoherent resumption shape.",
                    evidence={"connection_id": connection_id},
                )
            )
        negotiated_h2 = any(item.http_version == "2" for item in requests)
        if negotiated_h2 and "h2" not in fingerprint.alpn_protocols:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "TLS_ALPN_HTTP_VERSION_CONFLICT",
                    "Negotiated HTTP version conflicts with offered ALPN",
                    "Requests arrived as HTTP/2 although the correlated ClientHello did not offer h2.",
                    evidence={
                        "connection_id": connection_id,
                        "alpn_offered": list(fingerprint.alpn_protocols),
                    },
                )
            )
    if lifecycle:
        findings.append(
            _finding(
                SignalClass.INFO,
                "TLS_LIFECYCLE_CAPTURED",
                "TLS connection lifecycle captured",
                "ClientHello identity, resumption extensions, negotiated protocol and request reuse were correlated per connection.",
                evidence={
                    "connection_count": len(lifecycle),
                    "connections": lifecycle[:32],
                    "resumption_offer_count": sum(bool(item["psk_offered"]) for item in lifecycle),
                },
            )
        )

    navigation = next((request for request in session.requests if request.path == "/"), None)
    ua = navigation.first_header("user-agent") if navigation else ""
    chromium_declared = bool(ua and any(token in ua for token in ("Chrome/", "Chromium/", "Edg/")))
    representative = fingerprints[0]
    if chromium_declared and "h2" not in representative.alpn_protocols:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TLS_ALPN_UA_CONFLICT",
                "Chromium UA did not offer HTTP/2",
                "The declared Chromium-family User-Agent conflicts with the observed ALPN list.",
                evidence={"alpn": list(representative.alpn_protocols), "user_agent": ua},
            )
        )
    non_grease_ciphers = [
        value for value in representative.cipher_suites if (value & 0x0F0F) != 0x0A0A
    ]
    non_grease_extensions = [
        value for value in representative.extension_ids if (value & 0x0F0F) != 0x0A0A
    ]
    if chromium_declared and (len(non_grease_ciphers) < 8 or len(non_grease_extensions) < 10):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TLS_CLIENT_HELLO_TOO_SPARSE",
                "TLS ClientHello is sparse for declared browser",
                "Cipher or extension counts are lower than expected for a current full browser stack.",
                evidence={
                    "cipher_count": len(non_grease_ciphers),
                    "extension_count": len(non_grease_extensions),
                    "ja3": representative.ja3,
                    "ja4": representative.ja4,
                },
            )
        )
    if representative.server_name is None:
        findings.append(
            _finding(
                SignalClass.LOW,
                "TLS_SNI_MISSING",
                "SNI is absent",
                "The local connection used an IP literal or omitted SNI. This is valid locally but differs from most production browser traffic.",
                evidence={"ja4": representative.ja4},
            )
        )
    findings.append(
        _finding(
            SignalClass.INFO,
            "TLS_FINGERPRINT_CAPTURED",
            "TLS fingerprint captured",
            "The passive local proxy parsed the cleartext ClientHello without terminating or modifying TLS.",
            evidence={
                "ja3": representative.ja3,
                "ja4": representative.ja4,
                "alpn": list(representative.alpn_protocols),
                "cipher_count": len(non_grease_ciphers),
                "extension_count": len(non_grease_extensions),
            },
        )
    )
    return findings


def _tls_persona_shape(fingerprint: TLSFingerprint) -> tuple[object, ...]:
    def without_grease(values: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(value for value in values if (value & 0x0F0F) != 0x0A0A)

    return (
        without_grease(fingerprint.supported_versions),
        without_grease(fingerprint.cipher_suites),
        without_grease(fingerprint.supported_groups),
        without_grease(fingerprint.signature_algorithms),
        fingerprint.alpn_protocols,
    )
