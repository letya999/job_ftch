from __future__ import annotations

import json

from job_ftch.infrastructure.bypass.parity_audit import (
    classify_parity_surface,
    load_parity_raw,
    summarize_parity_payload,
)


def test_parity_audit_maps_v2_findings_to_surfaces() -> None:
    payload = {
        "session_id": "project-browser-hook-123",
        "client_name": "project-browser-hook",
        "summary": {
            "score": 55,
            "hard_count": 1,
            "medium_count": 1,
            "low_count": 0,
            "disposition": "fail",
        },
        "findings": [
            {
                "signal_class": "hard_bot_signal",
                "severity_score": 40,
                "code": "JS_NAVIGATOR_WEBDRIVER",
                "title": "webdriver",
                "reason": "navigator.webdriver is true",
            },
            {
                "signal_class": "medium_suspicious",
                "severity_score": 15,
                "code": "TLS_ALPN_UA_CONFLICT",
                "title": "ALPN conflict",
                "reason": "Chromium UA did not offer h2",
            },
        ],
    }

    summary = summarize_parity_payload(payload)

    assert not summary.ok
    assert summary.blocking_codes == ("JS_NAVIGATOR_WEBDRIVER", "TLS_ALPN_UA_CONFLICT")
    assert summary.surface_counts == {"runtime": 1, "tls": 1}


def test_parity_audit_loads_raw_json(tmp_path) -> None:
    raw = tmp_path / "raw.json"
    raw.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "client_name": "project-browser-hook",
                "summary": {"score": 0, "disposition": "pass"},
                "findings": [],
            }
        ),
        encoding="utf-8",
    )

    summary = load_parity_raw(raw)

    assert summary.ok
    assert summary.client_name == "project-browser-hook"


def test_parity_surface_classifier_handles_current_catalog() -> None:
    assert classify_parity_surface("NET_SEC_FETCH_ABSENT") == "network"
    assert classify_parity_surface("REALM_PARITY_CLASSIC_WORKER_RUNTIME_USERAGENT") == "realm"
    assert classify_parity_surface("BEHAVIOR_NO_EVENTS") == "behavior"
    assert classify_parity_surface("CDP_AUTOMATION_GLOBALS") == "cdp"
