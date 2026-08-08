from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from paritylab.behavior_replay import BehaviorReplayIndex
from paritylab.challenges import ChallengeEngine
from paritylab.config import LabConfig
from paritylab.gate import GateEngine, GatePolicy
from paritylab.models import (
    JsonValue,
    RequestRecord,
    json_safe,
    utc_now_iso,
)
from paritylab.protected_site import JobCatalog, classify_intent
from paritylab.report import render_markdown
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring import score_session
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CACHE_BODY = b"parity-lab-cache-v1\n"
CACHE_ETAG = '"' + hashlib.sha256(CACHE_BODY).hexdigest()[:24] + '"'
CACHE_LAST_MODIFIED = "Tue, 04 Aug 2026 00:00:00 GMT"
CLEARANCE_COOKIE = "parity_clearance"


@dataclass(slots=True)
class Playground:
    catalog: JobCatalog
    challenges: ChallengeEngine
    gate: GateEngine


def _query(scope: Scope) -> dict[str, list[str]]:
    raw = scope.get("query_string", b"")
    return parse_qs(raw.decode("latin-1"), keep_blank_values=True)


def _cookie_sid(scope: Scope) -> str | None:
    headers = Headers(scope=scope)
    raw = headers.get("cookie", "")
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return None
    morsel = cookie.get("parity_sid")
    return morsel.value if morsel else None


def _session_id(scope: Scope) -> str:
    query = _query(scope)
    candidates = [
        (query.get("sid") or [None])[0],
        Headers(scope=scope).get("x-parity-session"),
        _cookie_sid(scope),
    ]
    for candidate in candidates:
        if candidate and _SESSION_RE.fullmatch(candidate):
            return candidate
    return "unassigned"


def _query_bool(scope: Scope, name: str, default: bool = False) -> bool:
    value = (_query(scope).get(name) or [None])[0]
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _decode_headers(raw: list[tuple[bytes, bytes]]) -> tuple[tuple[str, str], ...]:
    return tuple((name.decode("latin-1"), value.decode("latin-1")) for name, value in raw)


class RequestAuditMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        store: ArtifactStore,
        registry: TLSConnectionRegistry,
        config: LabConfig,
    ) -> None:
        self.app = app
        self.store = store
        self.registry = registry
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_ns = time.monotonic_ns()
        request_id = uuid.uuid4().hex
        session_id = _session_id(scope)
        query_values = _query(scope)
        client_name = (query_values.get("client") or ["unknown"])[0]
        client_family = (query_values.get("family") or [client_name])[0]
        expected_failure = _query_bool(scope, "expected_failure")
        gate_enabled = _query_bool(scope, "gate")
        await self.store.ensure_session(
            session_id,
            client_name=client_name,
            client_family=client_family,
            expected_failure=expected_failure,
            gate_enabled=gate_enabled,
        )

        client = scope.get("client") or ("unknown", 0)
        client_host, client_port = str(client[0]), int(client[1])
        connection = await self.registry.lookup(client_port)
        if connection is not None:
            client_host = connection.client_host
            client_port = connection.client_port
            await self.store.add_tls(session_id, connection.fingerprint)

        request_body_bytes = 0
        response_body_bytes = 0
        response_status = 500
        response_headers: tuple[tuple[str, str], ...] = ()

        async def audited_receive() -> Message:
            nonlocal request_body_bytes
            message = await receive()
            if message["type"] == "http.request":
                request_body_bytes += len(message.get("body", b""))
            return message

        async def audited_send(message: Message) -> None:
            nonlocal response_body_bytes, response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                response_headers = _decode_headers(list(message.get("headers", [])))
            elif message["type"] == "http.response.body":
                response_body_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, audited_receive, audited_send)
        finally:
            duration_ms = (time.monotonic_ns() - started_ns) / 1_000_000
            raw_path = scope.get("raw_path", scope.get("path", "").encode("utf-8"))
            path = raw_path.decode("latin-1") if isinstance(raw_path, bytes) else str(raw_path)
            record = RequestRecord(
                request_id=request_id,
                session_id=session_id,
                observed_at=utc_now_iso(),
                monotonic_ns=started_ns,
                method=str(scope.get("method", "")),
                path=path,
                query=scope.get("query_string", b"").decode("latin-1"),
                scheme=str(scope.get("scheme", "")),
                http_version=str(scope.get("http_version", "")),
                client_host=client_host,
                client_port=client_port,
                connection_id=connection.connection_id if connection else None,
                tls_ja3=connection.fingerprint.ja3 if connection else None,
                tls_ja4=connection.fingerprint.ja4 if connection else None,
                headers=_decode_headers(list(scope.get("headers", []))),
                response_status=response_status,
                response_headers=response_headers,
                duration_ms=duration_ms,
                request_body_bytes=request_body_bytes,
                response_body_bytes=response_body_bytes,
            )
            await self.store.add_request(record)


def _common_headers(config: LabConfig) -> dict[str, str]:
    headers = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "same-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "cross-origin-opener-policy": "same-origin",
        "cross-origin-resource-policy": "same-origin",
        "server-timing": "paritylab;dur=1",
    }
    if config.enable_http3:
        headers["alt-svc"] = f'h3=":{config.public_port}"; ma=60'
    return headers


def _json_response(config: LabConfig, value: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(json_safe(value), status_code=status_code, headers=_common_headers(config))


def _safe_json_mapping(value: Any) -> dict[str, JsonValue]:
    safe = json_safe(value)
    return safe if isinstance(safe, dict) else {"value": safe}


def _safe_error_list(value: Any) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    output: list[dict[str, JsonValue]] = []
    for item in value[:200]:
        output.append(_safe_json_mapping(item))
    return tuple(output)


def create_app(
    config: LabConfig,
    *,
    store: ArtifactStore,
    registry: TLSConnectionRegistry,
) -> Starlette:
    reputation = OfflineIPReputation(config.ip_reputation_file, config.asn_database_file)
    replay_index = BehaviorReplayIndex(config.artifacts_dir / "_behavior_replay_index.jsonl")
    static_dir = config.static_dir.resolve()
    common = _common_headers(config)
    playground: Playground | None = None
    if config.playground_enabled:
        playground = Playground(
            catalog=JobCatalog(seed=config.playground_seed),
            challenges=ChallengeEngine(),
            gate=GateEngine(
                GatePolicy(
                    tarpit_delay_ms=config.playground_tarpit_delay_ms,
                    max_requests_per_window=config.playground_rate_limit,
                )
            ),
        )

    async def health(_: Request) -> JSONResponse:
        return _json_response(
            config,
            {
                "ok": True,
                "service": "bot-browser-parity-lab",
                "http3_configured": config.enable_http3,
                "time": utc_now_iso(),
            },
        )

    async def index(request: Request) -> Response:
        sid = request.query_params.get("sid")
        if not sid or not _SESSION_RE.fullmatch(sid):
            sid = uuid.uuid4().hex
            params = {
                "sid": sid,
                "client": request.query_params.get("client", "manual-browser"),
                "family": request.query_params.get("family", "manual"),
            }
            target = "/?" + "&".join(f"{key}={value}" for key, value in params.items())
            response = RedirectResponse(target, status_code=307, headers=common)
            response.set_cookie(
                "parity_sid",
                sid,
                secure=True,
                httponly=False,
                samesite="lax",
                max_age=3600,
            )
            return response
        state = await store.ensure_session(
            sid,
            client_name=request.query_params.get("client", "manual-browser"),
            client_family=request.query_params.get("family", "manual"),
            expected_failure=request.query_params.get("expected_failure") == "1",
            gate_enabled=request.query_params.get("gate") == "1",
        )
        baseline_profile = request.query_params.get("baseline_profile", "")[:128]
        if baseline_profile:
            state.metadata["baseline_profile"] = baseline_profile
        template = (static_dir / "index.html").read_text(encoding="utf-8")
        rendered = (
            template.replace("__SID__", sid)
            .replace("__CLIENT__", request.query_params.get("client", "manual-browser"))
            .replace("__FAMILY__", request.query_params.get("family", "manual"))
            .replace(
                "__STORAGE_FRAME_URL__",
                f"{config.backend_url}/fixtures/storage-frame?sid={sid}",
            )
            .replace("__EXPECTED_FAILURE__", request.query_params.get("expected_failure", "0"))
            .replace("__GATE__", request.query_params.get("gate", "0"))
        )
        response = HTMLResponse(rendered, headers={**common, "cache-control": "no-store"})
        response.set_cookie(
            "parity_sid",
            sid,
            secure=True,
            httponly=False,
            samesite="lax",
            max_age=3600,
        )
        return response

    async def static_file(request: Request) -> Response:
        relative = request.path_params["name"]
        candidate = (static_dir / relative).resolve()
        if static_dir not in candidate.parents or not candidate.is_file():
            return Response("not found", status_code=404, headers=common)
        content_type, _ = mimetypes.guess_type(candidate.name)
        headers = {**common, "cache-control": "public, max-age=60"}
        return FileResponse(candidate, media_type=content_type, headers=headers)

    async def favicon(request: Request) -> Response:
        return await static_file_with_name(request, "favicon.svg", common, static_dir)

    async def static_file_with_name(
        request: Request,
        name: str,
        headers: dict[str, str],
        directory: Path,
    ) -> Response:
        del request
        return FileResponse(directory / name, media_type="image/svg+xml", headers=headers)

    async def fetch_probe(request: Request) -> JSONResponse:
        return _json_response(
            config,
            {
                "ok": True,
                "method": request.method,
                "http_version": request.scope.get("http_version"),
                "cookies": dict(request.cookies),
                "server_time": utc_now_iso(),
                "request_headers": list(request.headers.items()),
            },
        )

    async def set_cookie(_: Request) -> JSONResponse:
        response = _json_response(config, {"ok": True, "set": ["lab_cookie", "strict_cookie"]})
        response.set_cookie(
            "lab_cookie",
            "present",
            secure=True,
            httponly=False,
            samesite="lax",
            max_age=600,
        )
        response.set_cookie(
            "strict_cookie",
            "present",
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=600,
        )
        return response

    async def echo_cookie(request: Request) -> JSONResponse:
        return _json_response(config, {"ok": True, "cookies": dict(request.cookies)})

    async def redirect_start(request: Request) -> RedirectResponse:
        sid = request.query_params.get("sid", "unassigned")
        response = RedirectResponse(f"/api/redirect/mid?sid={sid}", status_code=302, headers=common)
        response.set_cookie("redirect_cookie", "hop1", secure=True, samesite="lax", max_age=300)
        return response

    async def redirect_mid(request: Request) -> RedirectResponse:
        sid = request.query_params.get("sid", "unassigned")
        return RedirectResponse(f"/api/redirect/final?sid={sid}", status_code=307, headers=common)

    async def redirect_final(_: Request) -> Response:
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="#394b59"/></svg>'
        return Response(
            svg, media_type="image/svg+xml", headers={**common, "cache-control": "no-store"}
        )

    async def cacheable(request: Request) -> Response:
        if request.headers.get("if-none-match") == CACHE_ETAG:
            return Response(
                status_code=304,
                headers={
                    **common,
                    "etag": CACHE_ETAG,
                    "last-modified": CACHE_LAST_MODIFIED,
                    "cache-control": "public, max-age=0, must-revalidate",
                },
            )
        return Response(
            CACHE_BODY,
            media_type="text/plain",
            headers={
                **common,
                "etag": CACHE_ETAG,
                "last-modified": CACHE_LAST_MODIFIED,
                "cache-control": "public, max-age=0, must-revalidate",
            },
        )

    async def no_store(_: Request) -> Response:
        return Response(
            b"no-store\n",
            media_type="text/plain",
            headers={**common, "cache-control": "no-store"},
        )

    async def delay(request: Request) -> Response:
        try:
            delay_ms = min(max(int(request.path_params["ms"]), 0), 1000)
        except ValueError:
            delay_ms = 0

        await asyncio.sleep(delay_ms / 1000)
        return Response(
            f"delay={delay_ms}\n",
            media_type="text/plain",
            headers={**common, "cache-control": "no-store"},
        )

    async def report(request: Request) -> JSONResponse:
        sid = request.path_params["sid"]
        state = await store.get(sid)
        if state is None:
            return _json_response(config, {"ok": False, "error": "session not found"}, 404)
        return _json_response(config, {"ok": True, "session": state})

    async def finish(request: Request) -> JSONResponse:
        sid = request.path_params["sid"]
        state = await store.get(sid)
        if state is None:
            return _json_response(config, {"ok": False, "error": "session not found"}, 404)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, Mapping):
            client_name = str(payload.get("client", state.client_name))[:128]
            client_family = str(payload.get("family", state.client_family))[:128]
            expected_failure = bool(payload.get("expectedFailure", state.expected_failure))
            gate_enabled = bool(payload.get("gate", state.gate_enabled))
            state.client_name = client_name
            state.client_family = client_family
            state.expected_failure = expected_failure
            state.gate_enabled = gate_enabled
            state.metadata.update(_safe_json_mapping(payload.get("metadata", {})))
        if playground is not None:
            drained = playground.challenges.drain_ledger(sid)
            if drained:
                state.challenges.extend(drained)
            state.intent = classify_intent(state.requests, playground.catalog)
        replay = await replay_index.assess_and_record(sid, state.behavior)
        state.metadata["behavior_replay"] = replay.to_json()
        findings, summary = score_session(state, reputation=reputation)
        markdown = render_markdown(state, findings, summary)
        finalized = await store.finalize(
            sid,
            findings=findings,
            summary=summary,
            markdown=markdown,
        )
        return _json_response(
            config,
            {
                "ok": True,
                "summary": summary,
                "artifact_dir": str((config.artifacts_dir / sid).resolve()),
                "finding_codes": [finding.code for finding in findings],
                "session": finalized,
            },
        )

    from paritylab.routes.fixtures import fixture_routes
    from paritylab.routes.observatory import observatory_routes
    from paritylab.routes.playground import playground_routes
    from paritylab.routes.probes import probe_routes
    from paritylab.routes.vendor import vendor_routes
    from paritylab.oss_registry import load_oss_registry

    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/", index, methods=["GET"]),
        Route("/favicon.ico", favicon, methods=["GET"]),
        Route("/static/{name:path}", static_file, methods=["GET"]),
        Route("/api/fetch", fetch_probe, methods=["GET", "POST"]),
        Route("/api/cookie/set", set_cookie, methods=["GET"]),
        Route("/api/cookie/echo", echo_cookie, methods=["GET"]),
        Route("/api/redirect/start", redirect_start, methods=["GET"]),
        Route("/api/redirect/mid", redirect_mid, methods=["GET"]),
        Route("/api/redirect/final", redirect_final, methods=["GET"]),
        Route("/api/cacheable", cacheable, methods=["GET"]),
        Route("/api/no-store", no_store, methods=["GET"]),
        Route("/api/delay/{ms:int}", delay, methods=["GET"]),
        Route("/api/report/{sid:str}", report, methods=["GET"]),
        Route("/api/finish/{sid:str}", finish, methods=["POST"]),
    ]
    routes.extend(probe_routes(config, store, common))
    routes.extend(observatory_routes(config, store))
    routes.extend(vendor_routes(config, store, load_oss_registry(config.oss_registry_file)))
    routes.extend(fixture_routes(config, common))
    if playground is not None:
        routes.extend(playground_routes(playground, config, common, store, registry))
    app = Starlette(debug=False, routes=routes)
    app.add_middleware(RequestAuditMiddleware, store=store, registry=registry, config=config)
    if playground is not None:
        app.state.playground = playground
    return app
