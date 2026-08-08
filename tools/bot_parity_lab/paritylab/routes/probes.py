from __future__ import annotations

import base64
import hashlib
import json
import math
import uuid
from collections import Counter
from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from paritylab.app import _json_response, _safe_error_list, _safe_json_mapping, _session_id
from paritylab.config import LabConfig
from paritylab.models import BehaviorEvent, OpaquePayloadRecord, ProbeRecord, utc_now_iso
from paritylab.store import ArtifactStore


def probe_routes(config: LabConfig, store: ArtifactStore, common: dict[str, str]) -> list[Route]:
    async def probe(request: Request) -> JSONResponse:
        sid = _session_id(request.scope)
        payload = await request.json()
        record = ProbeRecord(
            session_id=sid,
            observed_at=utc_now_iso(),
            realm=str(payload.get("realm", "unknown"))[:128],
            sequence=int(payload.get("sequence", 0)),
            data=_safe_json_mapping(payload.get("data", {})),
            errors=_safe_error_list(payload.get("errors", [])),
        )
        await store.add_probe(record)
        return _json_response(config, {"ok": True, "realm": record.realm})

    async def events(request: Request) -> JSONResponse:
        sid = _session_id(request.scope)
        payload = await request.json()
        raw_events = payload.get("events", []) if isinstance(payload, Mapping) else []
        records: list[BehaviorEvent] = []
        if isinstance(raw_events, list):
            for raw in raw_events[:5000]:
                if not isinstance(raw, Mapping):
                    continue
                records.append(
                    BehaviorEvent(
                        session_id=sid,
                        observed_at=utc_now_iso(),
                        sequence=int(raw.get("sequence", 0)),
                        event_type=str(raw.get("type", "unknown"))[:64],
                        client_ts_ms=float(raw.get("clientTsMs", 0.0)),
                        since_navigation_ms=float(raw.get("sinceNavigationMs", 0.0)),
                        trusted=(bool(raw["trusted"]) if raw.get("trusted") is not None else None),
                        data=_safe_json_mapping(raw.get("data", {})),
                    )
                )
        await store.add_behavior(records)
        return _json_response(config, {"ok": True, "accepted": len(records)})

    async def beacon(request: Request) -> Response:
        body = await request.body()
        return Response(
            status_code=204,
            headers={**common, "x-beacon-bytes": str(len(body)), "cache-control": "no-store"},
        )

    async def opaque(request: Request) -> JSONResponse:
        sid = _session_id(request.scope)
        body = await request.body()
        if len(body) > config.request_body_limit:
            return _json_response(config, {"ok": False, "error": "payload too large"}, 413)
        content_type = request.headers.get("content-type", "application/octet-stream")
        request_id = request.headers.get("x-opaque-id", uuid.uuid4().hex)
        record = OpaquePayloadRecord(
            session_id=sid,
            observed_at=utc_now_iso(),
            request_id=request_id[:128],
            content_type=content_type[:256],
            body_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            shannon_entropy=_shannon_entropy(body),
            printable_ratio=_printable_ratio(body),
            likely_base64=_likely_base64(body),
            likely_json=bool(_json_key_shape(body)) or body.lstrip().startswith((b"[", b"{")),
            key_shape=_json_key_shape(body),
        )
        await store.add_opaque(record)
        return _json_response(config, {"ok": True, "observation": record})

    return [
        Route("/api/probe", probe, methods=["POST"]),
        Route("/api/events", events, methods=["POST"]),
        Route("/api/beacon", beacon, methods=["POST"]),
        Route("/api/opaque", opaque, methods=["POST"]),
    ]


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 1.0
    printable = sum(byte in b"\t\r\n" or 32 <= byte < 127 for byte in data)
    return printable / len(data)


def _likely_base64(data: bytes) -> bool:
    compact = b"".join(data.split())
    if len(compact) < 16 or len(compact) % 4:
        return False
    allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"  # pragma: allowlist secret
    if any(byte not in allowed for byte in compact):
        return False
    try:
        base64.b64decode(compact.replace(b"-", b"+").replace(b"_", b"/"), validate=False)
    except Exception:
        return False
    return True


def _json_key_shape(data: bytes) -> tuple[str, ...]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except Exception:
        return ()
    if isinstance(parsed, Mapping):
        return tuple(sorted(str(key)[:64] for key in parsed)[:100])
    return ()
