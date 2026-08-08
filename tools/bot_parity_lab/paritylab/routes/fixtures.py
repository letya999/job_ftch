from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from paritylab.app import _json_response
from paritylab.config import LabConfig
from paritylab.protection_fixtures import get_fixture


def fixture_routes(config: LabConfig, common: dict[str, str]) -> list[Route]:
    async def storage_frame(_: Request) -> HTMLResponse:
        body = """<!doctype html><meta charset="utf-8"><script>
        (async () => {
          const result = {origin: location.origin};
          try {
            localStorage.setItem("parity-embedded", "1");
            result.localStorage = localStorage.getItem("parity-embedded") === "1";
            localStorage.removeItem("parity-embedded");
          } catch (error) { result.localStorage = false; result.localStorageError = error.name; }
          try {
            document.cookie = "parity_embed=1; Path=/; Secure; SameSite=None";
            result.cookie = document.cookie.includes("parity_embed=1");
          } catch (error) { result.cookie = false; result.cookieError = error.name; }
          result.indexedDB = typeof indexedDB !== "undefined";
          result.storageAccessAPI = typeof document.hasStorageAccess === "function";
          if (result.storageAccessAPI) {
            try { result.hasStorageAccess = await document.hasStorageAccess(); }
            catch (error) { result.hasStorageAccess = null; result.storageAccessError = error.name; }
          } else result.hasStorageAccess = null;
          parent.postMessage({type: "parity-storage-frame", result}, "*");
        })();
        </script>"""
        return HTMLResponse(
            body,
            headers={
                **common,
                "cache-control": "no-store",
                "cross-origin-resource-policy": "cross-origin",
                "x-parity-owned-fixture": "storage-frame",
            },
        )

    async def protection_fixture(request: Request) -> Response:
        fixture_id = str(request.path_params["fixture_id"])
        fixture = get_fixture(fixture_id)
        if fixture is None:
            return Response("unknown owned fixture", status_code=404, headers=common)
        return HTMLResponse(
            fixture.body,
            status_code=fixture.status_code,
            headers={
                **common,
                **fixture.headers,
                "cache-control": "no-store",
                "x-parity-owned-fixture": fixture.fixture_id,
            },
        )

    async def protection_contract(request: Request) -> JSONResponse:
        fixture_id = str(request.path_params["fixture_id"])
        fixture = get_fixture(fixture_id)
        if fixture is None:
            return _json_response(config, {"ok": False, "error": "unknown owned fixture"}, 404)
        contract = fixture.contract.public_payload() if fixture.contract else None
        return _json_response(
            config,
            {
                "ok": True,
                "fixture": fixture.fixture_id,
                "contract": contract,
                "solve_supported": False,
            },
        )

    return [
        Route("/fixtures/storage-frame", storage_frame, methods=["GET"]),
        Route("/fixtures/protection/{fixture_id:str}", protection_fixture, methods=["GET"]),
        Route("/api/protection/{fixture_id:str}/contract", protection_contract, methods=["GET"]),
    ]
