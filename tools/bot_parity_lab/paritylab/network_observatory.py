from __future__ import annotations

import hashlib
import json
import base64
import binascii
from dataclasses import dataclass
from typing import Any

HTTP2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


class ProtocolObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HTTP2Frame:
    frame_type: int
    flags: int
    stream_id: int
    payload_length: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class HTTP2Fingerprint:
    settings: tuple[tuple[int, int], ...]
    connection_window_updates: tuple[int, ...]
    frame_sequence: tuple[str, ...]
    pseudo_header_order: tuple[str, ...]
    raw: str
    sha256: str


@dataclass(frozen=True, slots=True)
class QUICFingerprint:
    version: str
    alpn: str
    transport_parameters: tuple[tuple[str, str], ...]
    session_resumed: bool | None
    early_data_accepted: bool | None
    retry_observed: bool | None
    migration_observed: bool | None
    raw: str
    sha256: str


@dataclass(frozen=True, slots=True)
class TLSLifecycleFingerprint:
    negotiated_version: str
    alpn: str
    resumed: bool | None
    early_data_accepted: bool | None
    new_session_tickets: int
    key_updates: int
    handshake_duration_ms: float | None
    raw: str
    sha256: str


@dataclass(frozen=True, slots=True)
class HTTP3Fingerprint:
    settings: tuple[tuple[int, int], ...]
    stream_types: tuple[int, ...]
    frame_sequence: tuple[str, ...]
    qpack_stream_shapes: tuple[str, ...]
    raw: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DNSFingerprint:
    transport: str
    query_count: int
    qtypes: tuple[tuple[str, int], ...]
    rcodes: tuple[tuple[str, int], ...]
    cache_hit_count: int
    median_duration_ms: float | None
    encrypted: bool | None
    query_shape_hashes: tuple[str, ...]
    raw: str
    sha256: str


@dataclass(frozen=True, slots=True)
class TCPIPFingerprint:
    ip_version: int
    initial_ttl: int
    observed_ttl: int
    hop_estimate: int
    tcp_window: int
    window_scale: int | None
    mss: int | None
    option_order: tuple[str, ...]
    sack_permitted: bool
    timestamps: bool
    ecn: bool
    syn_retransmissions: int
    pacing_ms: tuple[float, ...]
    raw: str
    sha256: str


def parse_http2_frames(data: bytes, *, require_preface: bool = True) -> tuple[HTTP2Frame, ...]:
    offset = 0
    if data.startswith(HTTP2_PREFACE):
        offset = len(HTTP2_PREFACE)
    elif require_preface:
        raise ProtocolObservationError("HTTP/2 client preface missing")
    frames: list[HTTP2Frame] = []
    while offset < len(data):
        if len(data) - offset < 9:
            raise ProtocolObservationError("truncated HTTP/2 frame header")
        length = int.from_bytes(data[offset : offset + 3], "big")
        frame_type = data[offset + 3]
        flags = data[offset + 4]
        stream_id = int.from_bytes(data[offset + 5 : offset + 9], "big") & 0x7FFFFFFF
        offset += 9
        end = offset + length
        if end > len(data):
            raise ProtocolObservationError("truncated HTTP/2 frame payload")
        payload = data[offset:end]
        frames.append(HTTP2Frame(frame_type, flags, stream_id, length, payload))
        offset = end
    return tuple(frames)


def fingerprint_http2(
    frames: tuple[HTTP2Frame, ...], *, pseudo_header_order: tuple[str, ...] = ()
) -> HTTP2Fingerprint:
    settings: list[tuple[int, int]] = []
    window_updates: list[int] = []
    sequence: list[str] = []
    for frame in frames:
        sequence.append(
            f"{frame.frame_type}:{frame.flags}:{frame.stream_id}:{frame.payload_length}"
        )
        if frame.frame_type == 4 and not (frame.flags & 0x1):
            if frame.stream_id != 0 or frame.payload_length % 6:
                raise ProtocolObservationError("invalid HTTP/2 SETTINGS frame")
            for offset in range(0, frame.payload_length, 6):
                setting_id = int.from_bytes(frame.payload[offset : offset + 2], "big")
                value = int.from_bytes(frame.payload[offset + 2 : offset + 6], "big")
                settings.append((setting_id, value))
        elif frame.frame_type == 8 and frame.stream_id == 0:
            if frame.payload_length != 4:
                raise ProtocolObservationError("invalid HTTP/2 WINDOW_UPDATE frame")
            window_updates.append(int.from_bytes(frame.payload, "big") & 0x7FFFFFFF)
    raw = "|".join(
        [
            ",".join(f"{key}:{value}" for key, value in settings),
            ",".join(str(value) for value in window_updates),
            ",".join(sequence),
            ",".join(pseudo_header_order),
        ]
    )
    return HTTP2Fingerprint(
        settings=tuple(settings),
        connection_window_updates=tuple(window_updates),
        frame_sequence=tuple(sequence),
        pseudo_header_order=pseudo_header_order,
        raw=raw,
        sha256=hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def fingerprint_quic(metadata: dict[str, Any]) -> QUICFingerprint:
    parameters = metadata.get("transport_parameters", {})
    if not isinstance(parameters, dict):
        raise ProtocolObservationError("QUIC transport_parameters must be an object")
    normalized = tuple(
        sorted((str(key), _stable_value(value)) for key, value in parameters.items())
    )
    version = str(metadata.get("version", "unknown"))
    alpn = str(metadata.get("alpn", "unknown"))
    lifecycle = (
        _optional_bool(metadata.get("session_resumed")),
        _optional_bool(metadata.get("early_data_accepted")),
        _optional_bool(metadata.get("retry_observed")),
        _optional_bool(metadata.get("migration_observed")),
    )
    raw = "|".join(
        [
            version,
            alpn,
            ",".join(f"{key}={value}" for key, value in normalized),
            ",".join("?" if value is None else str(int(value)) for value in lifecycle),
        ]
    )
    return QUICFingerprint(
        version=version,
        alpn=alpn,
        transport_parameters=normalized,
        session_resumed=lifecycle[0],
        early_data_accepted=lifecycle[1],
        retry_observed=lifecycle[2],
        migration_observed=lifecycle[3],
        raw=raw,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def fingerprint_tls_lifecycle(metadata: dict[str, Any]) -> TLSLifecycleFingerprint:
    negotiated_version = str(metadata.get("negotiated_version", "unknown"))
    alpn = str(metadata.get("alpn", "unknown"))
    resumed = _optional_bool(metadata.get("resumed"))
    early_data = _optional_bool(metadata.get("early_data_accepted"))
    tickets = _nonnegative_int(metadata.get("new_session_tickets", 0), "new_session_tickets")
    key_updates = _nonnegative_int(metadata.get("key_updates", 0), "key_updates")
    duration_raw = metadata.get("handshake_duration_ms")
    duration = None
    if duration_raw is not None:
        if isinstance(duration_raw, bool) or not isinstance(duration_raw, (int, float)):
            raise ProtocolObservationError("handshake_duration_ms must be numeric or null")
        duration = float(duration_raw)
        if duration < 0 or duration > 300_000:
            raise ProtocolObservationError("handshake_duration_ms is outside bounds")
    raw = "|".join(
        [
            negotiated_version,
            alpn,
            "?" if resumed is None else str(int(resumed)),
            "?" if early_data is None else str(int(early_data)),
            str(tickets),
            str(key_updates),
            "?" if duration is None else f"{duration:.3f}",
        ]
    )
    return TLSLifecycleFingerprint(
        negotiated_version,
        alpn,
        resumed,
        early_data,
        tickets,
        key_updates,
        duration,
        raw,
        hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def decode_quic_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    if offset < 0 or offset >= len(data):
        raise ProtocolObservationError("truncated QUIC variable integer")
    length = 1 << (data[offset] >> 6)
    if offset + length > len(data):
        raise ProtocolObservationError("truncated QUIC variable integer")
    value = data[offset] & 0x3F
    for byte in data[offset + 1 : offset + length]:
        value = (value << 8) | byte
    return value, offset + length


def fingerprint_http3_streams(streams: list[dict[str, Any]]) -> HTTP3Fingerprint:
    settings: list[tuple[int, int]] = []
    stream_types: list[int] = []
    frame_sequence: list[str] = []
    qpack_shapes: list[str] = []
    for item in streams[:256]:
        if not isinstance(item, dict):
            raise ProtocolObservationError("HTTP/3 streams must be objects")
        encoded = item.get("data_base64")
        if not isinstance(encoded, str):
            raise ProtocolObservationError("HTTP/3 stream data_base64 is required")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProtocolObservationError("invalid HTTP/3 stream base64") from exc
        if len(data) > 1_000_000:
            raise ProtocolObservationError("HTTP/3 stream exceeds one megabyte")
        offset = 0
        stream_type_raw = item.get("stream_type")
        if stream_type_raw is None:
            stream_type, offset = decode_quic_varint(data, 0)
        elif isinstance(stream_type_raw, int) and not isinstance(stream_type_raw, bool):
            stream_type = stream_type_raw
        else:
            raise ProtocolObservationError("HTTP/3 stream_type must be an integer")
        stream_types.append(stream_type)
        if stream_type in {2, 3}:
            digest = hashlib.sha256(data[offset:]).hexdigest()[:24]
            qpack_shapes.append(f"{stream_type}:{len(data) - offset}:{digest}")
            continue
        while offset < len(data):
            frame_type, offset = decode_quic_varint(data, offset)
            length, offset = decode_quic_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ProtocolObservationError("truncated HTTP/3 frame payload")
            payload = data[offset:end]
            frame_sequence.append(f"{stream_type}:{frame_type}:{length}")
            if frame_type == 4:
                cursor = 0
                while cursor < len(payload):
                    setting_id, cursor = decode_quic_varint(payload, cursor)
                    value, cursor = decode_quic_varint(payload, cursor)
                    settings.append((setting_id, value))
            offset = end
    raw = "|".join(
        [
            ",".join(f"{key}:{value}" for key, value in settings),
            ",".join(str(value) for value in stream_types),
            ",".join(frame_sequence),
            ",".join(qpack_shapes),
        ]
    )
    return HTTP3Fingerprint(
        tuple(settings),
        tuple(stream_types),
        tuple(frame_sequence),
        tuple(qpack_shapes),
        raw,
        hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def fingerprint_dns(metadata: dict[str, Any]) -> DNSFingerprint:
    transport = str(metadata.get("transport", "unknown")).lower()
    if transport not in {"udp", "tcp", "dot", "doh", "doq", "system", "unknown"}:
        raise ProtocolObservationError("unsupported DNS transport")
    query_count = _nonnegative_int(metadata.get("query_count", 0), "query_count")
    qtypes = _count_map(metadata.get("qtypes", {}), "qtypes")
    rcodes = _count_map(metadata.get("rcodes", {}), "rcodes")
    cache_hits = _nonnegative_int(metadata.get("cache_hit_count", 0), "cache_hit_count")
    if cache_hits > query_count:
        raise ProtocolObservationError("cache_hit_count exceeds query_count")
    duration_raw = metadata.get("median_duration_ms")
    duration = None
    if duration_raw is not None:
        if isinstance(duration_raw, bool) or not isinstance(duration_raw, (int, float)):
            raise ProtocolObservationError("median_duration_ms must be numeric or null")
        duration = float(duration_raw)
        if duration < 0 or duration > 300_000:
            raise ProtocolObservationError("median_duration_ms is outside bounds")
    encrypted = _optional_bool(metadata.get("encrypted"))
    hashes = metadata.get("query_shape_hashes", [])
    if not isinstance(hashes, list) or not all(
        isinstance(item, str)
        and 8 <= len(item) <= 128
        and all(character in "0123456789abcdefABCDEF" for character in item)
        for item in hashes
    ):
        raise ProtocolObservationError("query_shape_hashes must contain pre-redacted hashes")
    normalized_hashes = tuple(sorted(hashes[:1024]))
    raw = "|".join(
        [
            transport,
            str(query_count),
            ",".join(f"{key}:{value}" for key, value in qtypes),
            ",".join(f"{key}:{value}" for key, value in rcodes),
            str(cache_hits),
            "?" if duration is None else f"{duration:.3f}",
            "?" if encrypted is None else str(int(encrypted)),
            ",".join(normalized_hashes),
        ]
    )
    return DNSFingerprint(
        transport,
        query_count,
        qtypes,
        rcodes,
        cache_hits,
        duration,
        encrypted,
        normalized_hashes,
        raw,
        hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def fingerprint_tcpip(metadata: dict[str, Any]) -> TCPIPFingerprint:
    ip_version = _bounded_int(metadata.get("ip_version"), "ip_version", 4, 6)
    if ip_version not in {4, 6}:
        raise ProtocolObservationError("ip_version must be 4 or 6")
    initial_ttl = _bounded_int(metadata.get("initial_ttl"), "initial_ttl", 1, 255)
    observed_ttl = _bounded_int(metadata.get("observed_ttl"), "observed_ttl", 1, 255)
    if observed_ttl > initial_ttl:
        raise ProtocolObservationError("observed_ttl exceeds initial_ttl")
    tcp_window = _bounded_int(metadata.get("tcp_window"), "tcp_window", 0, 0xFFFF)
    scale_raw = metadata.get("window_scale")
    window_scale = None if scale_raw is None else _bounded_int(scale_raw, "window_scale", 0, 14)
    mss_raw = metadata.get("mss")
    mss = None if mss_raw is None else _bounded_int(mss_raw, "mss", 256, 65535)
    option_order_raw = metadata.get("option_order", [])
    if not isinstance(option_order_raw, list) or not all(
        isinstance(item, str) and 1 <= len(item) <= 32 for item in option_order_raw
    ):
        raise ProtocolObservationError("option_order must be a short string array")
    option_order = tuple(item.lower() for item in option_order_raw[:32])
    sack = _required_bool(metadata.get("sack_permitted"), "sack_permitted")
    timestamps = _required_bool(metadata.get("timestamps"), "timestamps")
    ecn = _required_bool(metadata.get("ecn"), "ecn")
    retransmissions = _bounded_int(
        metadata.get("syn_retransmissions", 0), "syn_retransmissions", 0, 100
    )
    pacing_raw = metadata.get("pacing_ms", [])
    if not isinstance(pacing_raw, list) or len(pacing_raw) > 256:
        raise ProtocolObservationError("pacing_ms must be a bounded numeric array")
    pacing: list[float] = []
    for value in pacing_raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolObservationError("pacing_ms must be a bounded numeric array")
        numeric = float(value)
        if numeric < 0 or numeric > 300_000:
            raise ProtocolObservationError("pacing_ms value is outside bounds")
        pacing.append(numeric)
    raw = "|".join(
        [
            str(ip_version),
            str(initial_ttl),
            str(observed_ttl),
            str(tcp_window),
            "?" if window_scale is None else str(window_scale),
            "?" if mss is None else str(mss),
            ",".join(option_order),
            str(int(sack)),
            str(int(timestamps)),
            str(int(ecn)),
            str(retransmissions),
            ",".join(f"{value:.3f}" for value in pacing),
        ]
    )
    return TCPIPFingerprint(
        ip_version,
        initial_ttl,
        observed_ttl,
        initial_ttl - observed_ttl,
        tcp_window,
        window_scale,
        mss,
        option_order,
        sack,
        timestamps,
        ecn,
        retransmissions,
        tuple(pacing),
        raw,
        hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )


def _stable_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ProtocolObservationError("QUIC lifecycle flags must be boolean or null")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolObservationError(f"{field} must be a non-negative integer")
    return value


def _count_map(value: Any, field: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict):
        raise ProtocolObservationError(f"{field} must be an object")
    output: list[tuple[str, int]] = []
    for key, count in value.items():
        output.append((str(key)[:32], _nonnegative_int(count, f"{field}.{key}")))
    return tuple(sorted(output))


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    result = _nonnegative_int(value, field)
    if result < minimum or result > maximum:
        raise ProtocolObservationError(f"{field} is outside bounds")
    return result


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolObservationError(f"{field} must be boolean")
    return value
