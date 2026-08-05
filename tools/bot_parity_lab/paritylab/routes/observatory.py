from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from paritylab.app import _json_response, _safe_json_mapping, _session_id
from paritylab.config import LabConfig
from paritylab.models import ProtocolObservation, json_safe, utc_now_iso
from paritylab.network_observatory import (
    ProtocolObservationError,
    fingerprint_http2,
    fingerprint_http3_streams,
    fingerprint_dns,
    fingerprint_quic,
    fingerprint_tls_lifecycle,
    fingerprint_tcpip,
    parse_http2_frames,
)
from paritylab.store import ArtifactStore

PARSER_VERSION = "1.0.0"


def observatory_routes(config: LabConfig, store: ArtifactStore) -> list[Route]:
    async def ingest(request: Request) -> JSONResponse:
        sid = _session_id(request.scope)
        protocol = request.path_params["protocol"].lower()
        body = await request.body()
        if len(body) > config.request_body_limit:
            return _json_response(config, {"ok": False, "error": "payload too large"}, 413)
        try:
            payload = await request.json()
            if not isinstance(payload, Mapping):
                raise ProtocolObservationError("observation payload must be an object")
            source = str(payload.get("source", "fixture"))[:64]
            if protocol == "http2":
                record = _http2_record(sid, source, payload)
            elif protocol == "quic":
                record = _quic_record(sid, source, payload)
            elif protocol == "tls":
                record = _tls_record(sid, source, payload)
            elif protocol == "http3":
                record = _http3_record(sid, source, payload)
            elif protocol == "dns":
                record = _dns_record(sid, source, payload)
            elif protocol == "tcpip":
                record = _tcpip_record(sid, source, payload)
            else:
                return _json_response(config, {"ok": False, "error": "unsupported protocol"}, 404)
        except (ValueError, TypeError, binascii.Error, ProtocolObservationError) as exc:
            return _json_response(config, {"ok": False, "error": str(exc)[:300]}, 400)
        await store.add_protocol_observation(record)
        return _json_response(config, {"ok": True, "observation": json_safe(record)})

    return [Route("/api/observatory/{protocol:str}", ingest, methods=["POST"])]


def _http2_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    encoded = payload.get("wire_base64")
    if not isinstance(encoded, str):
        raise ProtocolObservationError("HTTP/2 wire_base64 is required")
    wire = base64.b64decode(encoded, validate=True)
    if len(wire) > 1_000_000:
        raise ProtocolObservationError("HTTP/2 fixture exceeds one megabyte")
    pseudo = payload.get("pseudo_header_order", [])
    if not isinstance(pseudo, list) or not all(isinstance(item, str) for item in pseudo):
        raise ProtocolObservationError("pseudo_header_order must be a string array")
    fingerprint = fingerprint_http2(
        parse_http2_frames(wire), pseudo_header_order=tuple(pseudo[:16])
    )
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="http2",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "settings": [list(item) for item in fingerprint.settings],
                "connection_window_updates": list(fingerprint.connection_window_updates),
                "frame_sequence": list(fingerprint.frame_sequence[:256]),
                "pseudo_header_order": list(fingerprint.pseudo_header_order),
                "frame_count": len(fingerprint.frame_sequence),
            }
        ),
    )


def _quic_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ProtocolObservationError("QUIC metadata object is required")
    fingerprint = fingerprint_quic(metadata)
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="quic",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "version": fingerprint.version,
                "alpn": fingerprint.alpn,
                "transport_parameters": [list(item) for item in fingerprint.transport_parameters],
                "session_resumed": fingerprint.session_resumed,
                "early_data_accepted": fingerprint.early_data_accepted,
                "retry_observed": fingerprint.retry_observed,
                "migration_observed": fingerprint.migration_observed,
            }
        ),
    )


def _tls_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ProtocolObservationError("TLS metadata object is required")
    fingerprint = fingerprint_tls_lifecycle(metadata)
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="tls",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "negotiated_version": fingerprint.negotiated_version,
                "alpn": fingerprint.alpn,
                "resumed": fingerprint.resumed,
                "early_data_accepted": fingerprint.early_data_accepted,
                "new_session_tickets": fingerprint.new_session_tickets,
                "key_updates": fingerprint.key_updates,
                "handshake_duration_ms": fingerprint.handshake_duration_ms,
            }
        ),
    )


def _http3_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ProtocolObservationError("HTTP/3 streams array is required")
    fingerprint = fingerprint_http3_streams(streams)
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="http3",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "settings": [list(item) for item in fingerprint.settings],
                "stream_types": list(fingerprint.stream_types),
                "frame_sequence": list(fingerprint.frame_sequence),
                "qpack_stream_shapes": list(fingerprint.qpack_stream_shapes),
            }
        ),
    )


def _dns_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ProtocolObservationError("DNS metadata object is required")
    fingerprint = fingerprint_dns(metadata)
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="dns",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "transport": fingerprint.transport,
                "query_count": fingerprint.query_count,
                "qtypes": [list(item) for item in fingerprint.qtypes],
                "rcodes": [list(item) for item in fingerprint.rcodes],
                "cache_hit_count": fingerprint.cache_hit_count,
                "median_duration_ms": fingerprint.median_duration_ms,
                "encrypted": fingerprint.encrypted,
                "query_shape_hashes": list(fingerprint.query_shape_hashes),
            }
        ),
    )


def _tcpip_record(
    session_id: str, source: str, payload: Mapping[object, object]
) -> ProtocolObservation:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ProtocolObservationError("TCP/IP metadata object is required")
    fingerprint = fingerprint_tcpip(metadata)
    return ProtocolObservation(
        session_id=session_id,
        observed_at=utc_now_iso(),
        protocol="tcpip",
        source=source,
        parser_version=PARSER_VERSION,
        fingerprint=fingerprint.sha256,
        evidence=_safe_json_mapping(
            {
                "ip_version": fingerprint.ip_version,
                "initial_ttl": fingerprint.initial_ttl,
                "observed_ttl": fingerprint.observed_ttl,
                "hop_estimate": fingerprint.hop_estimate,
                "tcp_window": fingerprint.tcp_window,
                "window_scale": fingerprint.window_scale,
                "mss": fingerprint.mss,
                "option_order": list(fingerprint.option_order),
                "sack_permitted": fingerprint.sack_permitted,
                "timestamps": fingerprint.timestamps,
                "ecn": fingerprint.ecn,
                "syn_retransmissions": fingerprint.syn_retransmissions,
                "pacing_ms": list(fingerprint.pacing_ms),
            }
        ),
    )
