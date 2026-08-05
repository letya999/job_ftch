from __future__ import annotations

import hashlib
import asyncio
from http.cookies import SimpleCookie

from starlette.testclient import TestClient

from paritylab.app import CLEARANCE_COOKIE, create_app
from paritylab.config import LabConfig
from paritylab.models import GateDecision, ProbeRecord, utc_now_iso
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry


def _config(tmp_path) -> LabConfig:
    return LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
        playground_enabled=True,
        playground_rate_limit=100,
    )


def _pow_nonce(prefix: str, difficulty_bits: int) -> str:
    for nonce in range(100_000):
        digest = hashlib.sha256(f"{prefix}{nonce}".encode("utf-8")).digest()
        bits = 0
        for byte in digest:
            if byte == 0:
                bits += 8
                continue
            for bit in range(7, -1, -1):
                if byte & (1 << bit):
                    break
                bits += 1
            break
        if bits >= difficulty_bits:
            return str(nonce)
    raise AssertionError("test PoW nonce not found")


def _cookie_value(set_cookie: str, name: str) -> str:
    cookie = SimpleCookie()
    cookie.load(set_cookie)
    return cookie[name].value


def test_playground_issues_pow_and_clearance_allows_catalog_access(tmp_path) -> None:
    config = _config(tmp_path)
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())

    with TestClient(app) as client:
        blocked = client.get("/jobs?sid=session-a", follow_redirects=False)
        assert blocked.status_code == 403
        assert "Owned local proof-of-work challenge" in blocked.text

        challenge_id = next(iter(app.state.playground.challenges._pow))
        spec = client.get(f"/challenge/pow/{challenge_id}?sid=session-a").json()
        solved = client.post(
            "/api/challenge/pow/verify?sid=session-a",
            json={
                "challenge_id": challenge_id,
                "nonce": _pow_nonce(spec["prefix"], spec["difficulty_bits"]),
            },
        )
        assert solved.status_code == 200
        token = _cookie_value(solved.headers["set-cookie"], CLEARANCE_COOKIE)

        allowed = client.get("/jobs?sid=session-a", cookies={CLEARANCE_COOKIE: token})
        assert allowed.status_code == 200
        assert "Careers" in allowed.text
        assert "/trap/hot-content" in allowed.text


def test_playground_puzzle_route_sets_clearance(tmp_path) -> None:
    config = _config(tmp_path)
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())

    with TestClient(app) as client:
        spec = app.state.playground.challenges.issue_puzzle("session-a")
        public = client.get(f"/challenge/puzzle/{spec.challenge_id}?sid=session-a")
        assert public.status_code == 200
        payload = public.json()
        assert payload["instruction"] == "select every circle"
        assert payload["grid_svg"].startswith("<svg")

        solved = client.post(
            "/api/challenge/puzzle/verify?sid=session-a",
            json={
                "challenge_id": spec.challenge_id,
                "cells": list(spec.expected),
                "duration_ms": 500,
                "pointer_samples": 2,
            },
        )
        assert solved.status_code == 200
        assert CLEARANCE_COOKIE in solved.headers["set-cookie"]


def test_playground_report_classifies_intent_and_records_gate_decisions(tmp_path) -> None:
    config = _config(tmp_path)
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())
    token, _expires = app.state.playground.challenges.issue_clearance("session-a")

    with TestClient(app) as client:
        for page in (1, 2, 3):
            response = client.get(
                f"/api/jobs?page={page}&sid=session-a",
                cookies={CLEARANCE_COOKIE: token},
            )
            assert response.status_code == 200

        report = client.get("/api/playground/report/session-a")

    assert report.status_code == 200
    payload = report.json()
    assert payload["intent"]["intent"] == "api_harvest"
    assert payload["intent"]["api_requests"] == 3
    assert payload["gate_decisions"]["counts"] == {GateDecision.ALLOW.value: 3}
    assert {item["decision"] for item in payload["gate_decisions"]["recent"]} == {
        GateDecision.ALLOW.value
    }


def test_positive_hard_risk_revokes_clearance_at_protected_route(tmp_path) -> None:
    config = _config(tmp_path)
    store = ArtifactStore(config.artifacts_dir)
    app = create_app(config, store=store, registry=TLSConnectionRegistry())
    token, _expires = app.state.playground.challenges.issue_clearance("session-a")
    asyncio.run(
        store.add_probe(
            ProbeRecord(
                session_id="session-a",
                observed_at=utc_now_iso(),
                realm="window",
                sequence=1,
                data={"runtime": {"userAgent": "HeadlessChrome/140"}},
            )
        )
    )

    with TestClient(app) as client:
        denied = client.get("/jobs?sid=session-a", cookies={CLEARANCE_COOKIE: token})
        report = client.get("/api/playground/report/session-a")

    assert denied.status_code == 403
    assert denied.headers["x-parity-gate"] == "deny"
    recent = report.json()["gate_decisions"]["recent"]
    assert recent[-1]["reason_code"] == "LIVE_HARD_RISK"
    assert recent[-1]["detail"] == "JS_HEADLESS_UA"
