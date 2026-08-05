from __future__ import annotations

from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from paritylab.app import _json_response, _safe_json_mapping, _session_id
from paritylab.config import LabConfig
from paritylab.models import ProbeRecord, utc_now_iso
from paritylab.oss_registry import OSSRegistry, OSSRegistryError
from paritylab.store import ArtifactStore


def vendor_routes(
    config: LabConfig, store: ArtifactStore, registry: OSSRegistry
) -> list[Route]:
    async def ingest(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > min(config.request_body_limit, 512 * 1024):
            return _json_response(config, {"ok": False, "error": "payload too large"}, 413)
        component_id = request.path_params["component"].lower()
        try:
            component = registry.require_evidence_adapter(component_id)
            payload = await request.json()
            if not isinstance(payload, Mapping):
                raise ValueError("vendor evidence must be an object")
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("vendor result must be an object")
            sequence = int(payload.get("sequence", 0))
            if sequence < 0:
                raise ValueError("sequence must be non-negative")
            data = _safe_json_mapping(result)
        except (OSSRegistryError, ValueError, TypeError) as exc:
            return _json_response(config, {"ok": False, "error": str(exc)[:300]}, 400)
        record = ProbeRecord(
            session_id=_session_id(request.scope),
            observed_at=utc_now_iso(),
            realm=component.namespace,
            sequence=sequence,
            data={
                "component": component.component_id,
                "version": component.version,
                "mode": component.mode,
                "result": data,
            },
        )
        await store.add_probe(record)
        return _json_response(config, {"ok": True, "realm": record.realm})

    return [Route("/api/vendor/{component:str}", ingest, methods=["POST"])]
