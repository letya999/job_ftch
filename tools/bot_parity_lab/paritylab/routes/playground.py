from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from paritylab.app import Playground, _json_response, _session_id
from paritylab.config import LabConfig
from paritylab.models import GateDecision
from paritylab.gate_risk import assess_live_gate_risk
from paritylab.protected_site import classify_intent
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry

CLEARANCE_COOKIE = "parity_clearance"

_POW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Checking your browser</title></head>
<body>
<h1>Checking your browser&hellip;</h1>
<p>Owned local proof-of-work challenge. No external service is called.</p>
<p id="status">Solving challenge&hellip;</p>
<script>
(async () => {
  const challengeId = __CID_JSON__;
  const target = __TARGET_JSON__;
  const statusNode = document.getElementById("status");
  try {
    const response = await fetch("/challenge/pow/" + challengeId, {credentials: "same-origin"});
    const spec = await response.json();
    const encoder = new TextEncoder();
    const digestHex = async (text) => {
      const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
      return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, "0")).join("");
    };
    const leadingZeroBits = (hex) => {
      let bits = 0;
      for (const ch of hex) {
        const value = parseInt(ch, 16);
        if (value === 0) { bits += 4; continue; }
        for (let bit = 3; bit >= 0; bit -= 1) {
          if (value & (1 << bit)) return bits;
          bits += 1;
        }
        return bits;
      }
      return bits;
    };
    for (let nonce = 0; nonce < 5000000; nonce += 1) {
      const hex = await digestHex(spec.prefix + String(nonce));
      if (leadingZeroBits(hex) >= spec.difficulty_bits) {
        const verify = await fetch("/api/challenge/pow/verify", {
          method: "POST",
          credentials: "same-origin",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({challenge_id: challengeId, nonce: String(nonce)})
        });
        const outcome = await verify.json();
        if (outcome.ok) { location.replace(target); return; }
        statusNode.textContent = "Challenge rejected: " + (outcome.reason || "unknown");
        return;
      }
      if (nonce % 2000 === 0) await new Promise(resolve => setTimeout(resolve, 0));
    }
    statusNode.textContent = "Challenge search space exhausted.";
  } catch (error) {
    statusNode.textContent = "Challenge error: " + String(error);
  }
})();
</script>
</body></html>
"""

_PUZZLE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Interactive check</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; }
#grid svg rect.cell { fill: transparent; stroke: #999; cursor: pointer; }
#grid svg rect.cell.selected { fill: rgba(47, 111, 237, 0.25); stroke: #2f6fed; }
</style></head>
<body>
<h1>Interactive check</h1>
<p id="instruction">Select every cell that contains a <strong>circle</strong>, then verify.</p>
<div id="grid">__GRID_SVG__</div>
<button id="verify" type="button">Verify</button>
<p id="status"></p>
<script>
(() => {
  const challengeId = __CID_JSON__;
  const target = __TARGET_JSON__;
  const startedAt = performance.now();
  let pointerSamples = 0;
  const selected = new Set();
  const statusNode = document.getElementById("status");
  addEventListener("pointermove", () => { pointerSamples += 1; }, {passive: true});
  const svg = document.querySelector("#grid svg");
  const shapes = Array.from(svg.children).filter(node => node.tagName !== "rect" || !node.classList.contains("cell"));
  for (let index = 0; index < 9; index += 1) {
    const x = (index % 3) * 60;
    const y = Math.floor(index / 3) * 60;
    const cell = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    cell.setAttribute("x", String(x));
    cell.setAttribute("y", String(y));
    cell.setAttribute("width", "60");
    cell.setAttribute("height", "60");
    cell.setAttribute("class", "cell");
    cell.addEventListener("click", () => {
      if (selected.has(index)) { selected.delete(index); cell.classList.remove("selected"); }
      else { selected.add(index); cell.classList.add("selected"); }
    });
    svg.appendChild(cell);
  }
  document.getElementById("verify").addEventListener("click", async () => {
    statusNode.textContent = "Verifying…";
    const response = await fetch("/api/challenge/puzzle/verify", {
      method: "POST",
      credentials: "same-origin",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({
        challenge_id: challengeId,
        cells: Array.from(selected).sort((a, b) => a - b),
        duration_ms: performance.now() - startedAt,
        pointer_samples: pointerSamples
      })
    });
    const outcome = await response.json();
    if (outcome.ok) { location.replace(target); return; }
    statusNode.textContent = "Rejected: " + (outcome.reason || "unknown");
  });
})();
</script>
</body></html>
"""

def playground_routes(
    playground: Playground,
    config: LabConfig,
    common: dict[str, str],
    store: ArtifactStore,
    registry: TLSConnectionRegistry,
) -> list[Route]:
    catalog = playground.catalog
    challenges = playground.challenges
    gate = playground.gate

    def _gate_headers(decision: str) -> dict[str, str]:
        return {**common, "cache-control": "no-store", "x-parity-gate": decision}

    async def _gate_check(request: Request, path: str) -> Response | None:
        sid = _session_id(request.scope)
        state = await store.ensure_session(sid)
        client = request.scope.get("client") or ("unknown", 0)
        connection = await registry.lookup(int(client[1]))
        ja3 = connection.fingerprint.ja3 if connection else None
        ja4 = connection.fingerprint.ja4 if connection else None
        token = request.cookies.get(CLEARANCE_COOKIE, "")
        clearance_valid = False
        if token:
            clearance_valid, _reason = challenges.verify_clearance(token, sid)
        risk = assess_live_gate_risk(state)
        decision = gate.decide(
            path=path,
            requests=state.requests,
            ja3=ja3,
            ja4=ja4,
            clearance_valid=clearance_valid,
            pow_failures=challenges.pow_failure_count(sid),
            hard_risk_codes=risk.hard_codes,
            medium_risk_codes=risk.medium_codes,
        )
        await store.add_gate_decision(sid, decision)
        if decision.decision is GateDecision.ALLOW:
            return None
        return await _challenge_response(request, decision.decision, sid, path)

    async def _challenge_response(
        request: Request, decision: GateDecision, sid: str, path: str
    ) -> Response:
        query = request.scope.get("query_string", b"").decode("latin-1")
        target = path + (f"?{query}" if query else "")
        if decision is GateDecision.DENY:
            return Response("denied", status_code=403, headers=_gate_headers("deny"))
        if decision is GateDecision.TARPIT:
            await asyncio.sleep(playground.gate.policy.tarpit_delay_ms / 1000)
            return Response("slow down", status_code=429, headers=_gate_headers("tarpit"))
        is_api = path.startswith("/api/")
        if decision is GateDecision.JS_CHALLENGE:
            spec = challenges.issue_pow(sid)
            if is_api:
                return _json_response(
                    config,
                    {"ok": False, "challenge": "pow", "challenge_url": f"/challenge/pow/{spec.challenge_id}"},
                    403,
                )
            page = _POW_PAGE.replace("__CID_JSON__", json.dumps(spec.challenge_id)).replace(
                "__TARGET_JSON__", json.dumps(target)
            )
            return HTMLResponse(page, status_code=403, headers=_gate_headers("js_challenge"))
        spec_puzzle = challenges.issue_puzzle(sid)
        if is_api:
            return _json_response(
                config,
                {
                    "ok": False,
                    "challenge": "puzzle",
                    "challenge_url": f"/challenge/puzzle/{spec_puzzle.challenge_id}",
                },
                403,
            )
        page = (
            _PUZZLE_PAGE.replace("__GRID_SVG__", spec_puzzle.grid_svg())
            .replace("__CID_JSON__", json.dumps(spec_puzzle.challenge_id))
            .replace("__TARGET_JSON__", json.dumps(target))
        )
        return HTMLResponse(page, status_code=403, headers=_gate_headers("interactive_challenge"))

    async def _with_clearance(request: Request, ok_payload: dict[str, Any]) -> Response:
        sid = _session_id(request.scope)
        token, exp = challenges.issue_clearance(sid)
        response = _json_response(config, ok_payload)
        response.set_cookie(
            CLEARANCE_COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=max(1, int(exp - time.time())),
        )
        return response

    async def robots(_: Request) -> Response:
        return Response(
            catalog.robots_txt(), media_type="text/plain; charset=utf-8", headers=_gate_headers("allow")
        )

    async def sitemap(_: Request) -> Response:
        return Response(
            catalog.sitemap_xml(), media_type="application/xml; charset=utf-8", headers=_gate_headers("allow")
        )

    async def jobs_listing(request: Request) -> Response:
        blocked = await _gate_check(request, "/jobs")
        if blocked is not None:
            return blocked
        try:
            page = max(1, min(int(request.query_params.get("page", "1")), catalog.pages))
        except ValueError:
            page = 1
        return HTMLResponse(catalog.listing_html(page), headers=_gate_headers("allow"))

    async def job_detail(request: Request) -> Response:
        job_id = str(request.path_params["job_id"])
        blocked = await _gate_check(request, f"/jobs/{job_id}")
        if blocked is not None:
            return blocked
        if catalog.job(job_id) is None:
            return Response("not found", status_code=404, headers=_gate_headers("allow"))
        return HTMLResponse(catalog.detail_html(job_id), headers=_gate_headers("allow"))

    async def api_jobs(request: Request) -> Response:
        blocked = await _gate_check(request, "/api/jobs")
        if blocked is not None:
            return blocked
        try:
            page = max(1, min(int(request.query_params.get("page", "1")), catalog.pages))
        except ValueError:
            page = 1
        return _json_response(config, catalog.api_listing_json(page))

    async def api_job_detail(request: Request) -> Response:
        job_id = str(request.path_params["job_id"])
        blocked = await _gate_check(request, f"/api/jobs/{job_id}")
        if blocked is not None:
            return blocked
        if catalog.job(job_id) is None:
            return _json_response(config, {"ok": False, "error": "not found"}, 404)
        return _json_response(config, catalog.api_detail_json(job_id))

    async def trap(request: Request) -> Response:
        sid = _session_id(request.scope)
        await store.record_trap_hit(sid, str(request.url.path))
        body = (
            "<!doctype html><html><head><title>mirror</title></head><body>"
            "<h1>Internal mirror</h1><p>Owned honeypot content. This path is not linked "
            "from any visible navigation.</p></body></html>"
        )
        return HTMLResponse(body, headers=_gate_headers("allow"))

    async def pow_spec_endpoint(request: Request) -> Response:
        spec = challenges.pow_spec(str(request.path_params["challenge_id"]))
        if spec is None:
            return _json_response(config, {"ok": False, "error": "unknown challenge"}, 404)
        return _json_response(config, {"ok": True, **challenges.pow_public(spec)})

    async def pow_verify(request: Request) -> Response:
        payload = await request.json()
        challenge_id = str(payload.get("challenge_id", ""))
        nonce = str(payload.get("nonce", ""))
        ok, reason = challenges.verify_pow(challenge_id, nonce)
        if not ok:
            return _json_response(config, {"ok": False, "reason": reason}, 403)
        return await _with_clearance(request, {"ok": True, "reason": reason})

    async def puzzle_spec_endpoint(request: Request) -> Response:
        spec = challenges.puzzle_spec(str(request.path_params["challenge_id"]))
        if spec is None:
            return _json_response(config, {"ok": False, "error": "unknown challenge"}, 404)
        return _json_response(
            config,
            {
                "ok": True,
                "challenge_id": spec.challenge_id,
                "instruction": "select every circle",
                "grid_svg": spec.grid_svg(),
                "deadline_seconds": max(0.0, spec.deadline - challenges.clock()),
            },
        )

    async def puzzle_verify(request: Request) -> Response:
        payload = await request.json()
        ok, reason = challenges.verify_puzzle(
            str(payload.get("challenge_id", "")),
            payload.get("cells"),
            duration_ms=float(payload.get("duration_ms", 0.0)),
            pointer_samples=int(payload.get("pointer_samples", 0)),
        )
        if not ok:
            return _json_response(config, {"ok": False, "reason": reason}, 403)
        return await _with_clearance(request, {"ok": True, "reason": reason})

    async def playground_report(request: Request) -> JSONResponse:
        sid = str(request.path_params["sid"])
        state = await store.get(sid)
        if state is None:
            return _json_response(config, {"ok": False, "error": "session not found"}, 404)
        intent = classify_intent(state.requests, catalog)
        decision_counts = Counter(item.decision.value for item in state.gate_decisions)
        return _json_response(
            config,
            {
                "ok": True,
                "intent": intent,
                "gate_decisions": {
                    "counts": dict(decision_counts),
                    "recent": state.gate_decisions[-50:],
                },
                "challenges": state.challenges,
                "challenge_engine": challenges.snapshot(),
                "fingerprint_realms": sorted({probe.realm for probe in state.probes}),
                "trap_hits": list(state.trap_hits),
            },
        )

    return [
        Route("/robots.txt", robots, methods=["GET"]),
        Route("/sitemap.xml", sitemap, methods=["GET"]),
        Route("/jobs", jobs_listing, methods=["GET"]),
        Route("/jobs/{job_id:str}", job_detail, methods=["GET"]),
        Route("/api/jobs", api_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id:str}", api_job_detail, methods=["GET"]),
        Route("/trap/{name:path}", trap, methods=["GET"]),
        Route("/internal/{name:path}", trap, methods=["GET"]),
        Route("/challenge/pow/{challenge_id:str}", pow_spec_endpoint, methods=["GET"]),
        Route("/challenge/puzzle/{challenge_id:str}", puzzle_spec_endpoint, methods=["GET"]),
        Route("/api/challenge/pow/verify", pow_verify, methods=["POST"]),
        Route("/api/challenge/puzzle/verify", puzzle_verify, methods=["POST"]),
        Route("/api/playground/report/{sid:str}", playground_report, methods=["GET"]),
    ]
