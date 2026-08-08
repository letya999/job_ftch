from __future__ import annotations

import json

from paritylab.models import (
    BehaviorEvent,
    GateDisposition,
    ProbeRecord,
    RequestRecord,
    SessionState,
    TLSFingerprint,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring import score_session
from paritylab.scoring.common import _catalog_snapshot


def _reputation(tmp_path):
    path = tmp_path / "reputation.json"
    path.write_text(json.dumps({"networks": []}), encoding="utf-8")
    return OfflineIPReputation(path)


def _request(session: SessionState, request_id: str, path: str, monotonic_ns: int) -> RequestRecord:
    return RequestRecord(
        request_id=request_id,
        session_id=session.session_id,
        observed_at="2026-08-05T00:00:00Z",
        monotonic_ns=monotonic_ns,
        method="GET",
        path=path,
        query="",
        scheme="https",
        http_version="2",
        client_host="127.0.0.1",
        client_port=1,
        connection_id=None,
        tls_ja3=None,
        tls_ja4=None,
        headers=(),
        response_status=200,
        response_headers=(),
        duration_ms=1.0,
    )


def test_empty_automated_session_fails_gate(tmp_path) -> None:
    session = SessionState(
        session_id="empty",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    findings, summary = score_session(session, reputation=_reputation(tmp_path))
    codes = {finding.code for finding in findings}
    assert "JS_WINDOW_PROBE_MISSING" in codes
    assert "BEHAVIOR_NO_EVENTS" in codes
    assert summary.disposition is GateDisposition.FAIL
    assert summary.hard_count >= 1


def test_negative_control_is_expected_failure(tmp_path) -> None:
    session = SessionState(
        session_id="negative",
        client_name="raw-httpx",
        client_family="httpx",
        expected_failure=True,
        gate_enabled=False,
    )
    _, summary = score_session(session, reputation=_reputation(tmp_path))
    assert summary.disposition is GateDisposition.EXPECTED_FAIL


def test_tls_drift_ignores_unrelated_control_connections(tmp_path) -> None:
    session = SessionState(
        session_id="browser",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.requests.append(
        RequestRecord(
            request_id="r1",
            session_id=session.session_id,
            observed_at="2026-08-04T00:00:00Z",
            monotonic_ns=1,
            method="GET",
            path="/",
            query="",
            scheme="https",
            http_version="2",
            client_host="127.0.0.1",
            client_port=1111,
            connection_id="browser-conn",
            tls_ja3="browser-ja3",
            tls_ja4="browser-ja4",
            headers=(
                ("user-agent", "Mozilla/5.0 Chrome/145.0.0.0"),
                ("sec-fetch-dest", "document"),
            ),
            response_status=200,
            response_headers=(),
            duration_ms=1.0,
        )
    )
    session.tls_fingerprints.extend(
        [
            TLSFingerprint(
                connection_id="browser-conn",
                observed_at="2026-08-04T00:00:00Z",
                client_host="127.0.0.1",
                client_port=1111,
                backend_source_port=2222,
                record_version=0x0303,
                legacy_version=0x0303,
                supported_versions=(0x0304, 0x0303),
                cipher_suites=tuple(range(16)),
                extension_ids=tuple(range(16)),
                supported_groups=(),
                ec_point_formats=(),
                signature_algorithms=(),
                alpn_protocols=("h2", "http/1.1"),
                server_name="localhost",
                ja3_raw="",
                ja3="browser-ja3",
                ja4_raw="",
                ja4="browser-ja4",
            ),
            TLSFingerprint(
                connection_id="control-conn",
                observed_at="2026-08-04T00:00:01Z",
                client_host="127.0.0.1",
                client_port=3333,
                backend_source_port=4444,
                record_version=0x0303,
                legacy_version=0x0303,
                supported_versions=(0x0304, 0x0303),
                cipher_suites=tuple(range(8)),
                extension_ids=tuple(range(8)),
                supported_groups=(),
                ec_point_formats=(),
                signature_algorithms=(),
                alpn_protocols=("http/1.1",),
                server_name="localhost",
                ja3_raw="",
                ja3="control-ja3",
                ja4_raw="",
                ja4="control-ja4",
            ),
        ]
    )

    findings, _summary = score_session(session, reputation=_reputation(tmp_path))

    codes = {finding.code for finding in findings}
    assert "TLS_FINGERPRINT_DRIFT" not in codes
    assert "TLS_LIFECYCLE_CAPTURED" in codes


def test_notification_default_matches_permissions_prompt(tmp_path) -> None:
    session = SessionState(
        session_id="notifications",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.probes.append(
        ProbeRecord(
            session_id=session.session_id,
            observed_at="2026-08-04T00:00:00Z",
            realm="window",
            sequence=1,
            data={
                "permissions": {"states": {"notifications": "prompt"}},
                "notifications": {"permission": "default"},
            },
        )
    )

    findings, _summary = score_session(session, reputation=_reputation(tmp_path))

    assert "JS_PERMISSION_NOTIFICATION_CONFLICT" not in {finding.code for finding in findings}


def test_notification_real_permission_conflict_is_still_detected(tmp_path) -> None:
    session = SessionState(
        session_id="notifications",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.probes.append(
        ProbeRecord(
            session_id=session.session_id,
            observed_at="2026-08-04T00:00:00Z",
            realm="window",
            sequence=1,
            data={
                "permissions": {"states": {"notifications": "granted"}},
                "notifications": {"permission": "denied"},
            },
        )
    )

    findings, _summary = score_session(session, reputation=_reputation(tmp_path))

    assert "JS_PERMISSION_NOTIFICATION_CONFLICT" in {finding.code for finding in findings}


def test_full_scorer_includes_deep_catalog_findings(tmp_path) -> None:
    session = SessionState(
        session_id="catalog",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.requests.append(
        RequestRecord(
            request_id="r1",
            session_id=session.session_id,
            observed_at="2026-08-04T00:00:00Z",
            monotonic_ns=1,
            method="GET",
            path="/",
            query="",
            scheme="https",
            http_version="2",
            client_host="127.0.0.1",
            client_port=1111,
            connection_id="browser-conn",
            tls_ja3="browser-ja3",
            tls_ja4="browser-ja4",
            headers=(
                ("host", "localhost"),
                ("accept", "text/html"),
                ("accept-language", "en-US,en;q=0.9"),
                ("accept-encoding", "gzip, deflate, br"),
                ("user-agent", "Mozilla/5.0 Chrome/150.0.0.0 Safari/537.36"),
                ("sec-fetch-site", "none"),
                ("sec-fetch-mode", "navigate"),
                ("sec-fetch-dest", "document"),
                ("sec-ch-ua", '"Chromium";v="150"'),
                ("sec-ch-ua-mobile", "?0"),
                ("sec-ch-ua-platform", '"Windows"'),
            ),
            response_status=200,
            response_headers=(),
            duration_ms=1.0,
        )
    )
    session.tls_fingerprints.append(
        TLSFingerprint(
            connection_id="browser-conn",
            observed_at="2026-08-04T00:00:00Z",
            client_host="127.0.0.1",
            client_port=1111,
            backend_source_port=2222,
            record_version=0x0303,
            legacy_version=0x0303,
            supported_versions=(0x0304, 0x0303),
            cipher_suites=tuple(range(16)),
            extension_ids=tuple(range(16)),
            supported_groups=(),
            ec_point_formats=(),
            signature_algorithms=(),
            alpn_protocols=("h2", "http/1.1"),
            server_name="localhost",
            ja3_raw="",
            ja3="browser-ja3",
            ja4_raw="",
            ja4="browser-ja4",
        )
    )
    session.probes.append(
        ProbeRecord(
            session_id=session.session_id,
            observed_at="2026-08-04T00:00:00Z",
            realm="window",
            sequence=1,
            data={
                "runtime": {
                    "userAgent": "Mozilla/5.0 Chrome/150.0.0.0 Safari/537.36",
                    "platform": "Win32",
                    "language": "en-US",
                    "languages": ["en-US", "en"],
                    "hardwareConcurrency": 8,
                    "deviceMemory": 8,
                    "userAgentData": {
                        "brands": [{"brand": "Chromium", "version": "150"}],
                        "platform": "Windows",
                        "highEntropy": {
                            "architecture": "x86",
                            "bitness": "64",
                            "brands": [{"brand": "Chromium", "version": "150"}],
                            "fullVersionList": [{"brand": "Chromium", "version": "150.0.0.0"}],
                            "uaFullVersion": "150.0.0.0",
                        },
                    },
                },
                "locale": {"timezone": "America/New_York"},
                "storage": {"estimate": {"quota": 5_000_000_000, "usage": 1234567}},
                "canvas": {"hash": "canvas"},
                "audio": {"hash": "audio"},
                "webgl": {
                    "unmaskedVendor": "Google Inc.",
                    "unmaskedRenderer": "ANGLE (Intel)",
                    "extensions": [f"EXT_{index}" for index in range(12)],
                },
                "chrome": {"exists": True, "runtimeExists": False},
                "codeIntegrity": {"functionToStringNative": True, "nonNativeExpected": []},
                "automation": {"suspiciousGlobals": [], "stackMarkers": []},
            },
        )
    )

    findings, _summary = score_session(session, reputation=_reputation(tmp_path))

    assert "CAT_STORAGE_MAGIC_USAGE" in {finding.code for finding in findings}


def test_behavior_timestamp_regression_is_detected(tmp_path) -> None:
    session = SessionState(
        session_id="time-regression",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.behavior.extend(
        [
            BehaviorEvent(
                session.session_id,
                "2026-08-05T00:00:00Z",
                1,
                "pointermove",
                10,
                250,
                True,
                {"x": 10, "y": 10},
            ),
            BehaviorEvent(
                session.session_id,
                "2026-08-05T00:00:00Z",
                2,
                "pointermove",
                11,
                120,
                True,
                {"x": 15, "y": 12},
            ),
        ]
    )

    findings, summary = score_session(session, reputation=_reputation(tmp_path))

    assert "BEHAVIOR_TIMESTAMP_REGRESSION" in {finding.code for finding in findings}
    assert summary.disposition is GateDisposition.FAIL


def test_compressed_pointer_teleports_are_detected(tmp_path) -> None:
    session = SessionState(
        session_id="compressed-path",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    events = [
        ("pointermove", 100.0, 0, 0),
        ("pointerdown", 100.4, 0, 0),
        ("pointermove", 101.0, 700, 0),
        ("pointerup", 101.4, 700, 0),
        ("click", 101.8, 700, 0),
        ("pointermove", 102.2, 0, 700),
        ("wheel", 102.8, 0, 700),
        ("scroll", 103.2, 0, 700),
    ]
    session.behavior.extend(
        BehaviorEvent(
            session.session_id,
            "2026-08-05T00:00:00Z",
            index,
            event_type,
            timestamp,
            timestamp,
            True,
            {"x": x, "y": y},
        )
        for index, (event_type, timestamp, x, y) in enumerate(events, start=1)
    )

    findings, summary = score_session(session, reputation=_reputation(tmp_path))

    codes = {finding.code for finding in findings}
    assert "BEHAVIOR_EVENT_BURST_COMPRESSED" in codes
    assert "BEHAVIOR_POINTER_TELEPORTS" in codes
    assert summary.disposition is GateDisposition.FAIL


def test_session_network_runtime_identity_conflicts_are_detected(tmp_path) -> None:
    session = SessionState(
        session_id="identity-conflict",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.requests.extend(
        [
            RequestRecord(
                "r1",
                session.session_id,
                "2026-08-05T00:00:00Z",
                1,
                "GET",
                "/",
                "",
                "https",
                "2",
                "127.0.0.1",
                1,
                None,
                None,
                None,
                (
                    ("user-agent", "Mozilla/5.0 Chrome/150.0.0.0"),
                    ("accept-language", "en-US,en;q=0.9"),
                ),
                200,
                (),
                1.0,
            ),
            RequestRecord(
                "r2",
                session.session_id,
                "2026-08-05T00:00:00Z",
                2,
                "GET",
                "/api/fetch",
                "",
                "https",
                "2",
                "127.0.0.1",
                1,
                None,
                None,
                None,
                (
                    ("user-agent", "Mozilla/5.0 Firefox/150.0"),
                    ("accept-language", "ru-RU,ru;q=0.9"),
                ),
                200,
                (),
                1.0,
            ),
        ]
    )
    session.probes.append(
        ProbeRecord(
            session.session_id,
            "2026-08-05T00:00:00Z",
            "window",
            1,
            {"runtime": {"userAgent": "Mozilla/5.0 Firefox/150.0", "language": "ru-RU"}},
            ({"stage": "canvas"},),
        )
    )

    findings, summary = score_session(session, reputation=_reputation(tmp_path))

    codes = {finding.code for finding in findings}
    assert {
        "SESSION_NETWORK_RUNTIME_UA_MISMATCH",
        "SESSION_NETWORK_RUNTIME_LANGUAGE_CONFLICT",
        "SESSION_REQUEST_UA_DRIFT",
        "SESSION_REQUEST_LANGUAGE_DRIFT",
        "SESSION_PRIMARY_PROBE_ERRORS",
    } <= codes
    assert summary.disposition is GateDisposition.FAIL


def test_transaction_and_probe_sequence_integrity_is_detected(tmp_path) -> None:
    session = SessionState(
        session_id="broken-transaction",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
    )
    session.requests.extend(
        [
            _request(session, "cookie-echo", "/api/cookie/echo", 10),
            _request(session, "cookie-set", "/api/cookie/set", 20),
            _request(session, "redirect-final", "/api/redirect/final", 30),
            _request(session, "redirect-start", "/api/redirect/start", 40),
            _request(session, "redirect-mid", "/api/redirect/mid", 50),
        ]
    )
    session.probes.extend(
        [
            ProbeRecord(session.session_id, "2026-08-05T00:00:00Z", "window", 2, {}),
            ProbeRecord(session.session_id, "2026-08-05T00:00:01Z", "iframe", 2, {}),
        ]
    )

    findings, summary = score_session(session, reputation=_reputation(tmp_path))

    codes = {finding.code for finding in findings}
    assert {
        "NET_COOKIE_TRANSACTION_ORDER",
        "NET_REDIRECT_CHAIN_ORDER",
        "SESSION_PROBE_SEQUENCE_INVALID",
    } <= codes
    assert summary.disposition is GateDisposition.FAIL


def test_catalog_uses_final_activation_for_post_interaction_scoring() -> None:
    session = SessionState(
        session_id="activation",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=False,
    )
    session.probes.extend(
        [
            ProbeRecord(
                session.session_id,
                "2026-08-05T00:00:00Z",
                "window",
                1,
                {"userActivation": {"hasBeenActive": False, "isActive": False}},
            ),
            ProbeRecord(
                session.session_id,
                "2026-08-05T00:00:01Z",
                "window-final",
                2,
                {"userActivation": {"hasBeenActive": True, "isActive": False}},
            ),
        ]
    )

    snapshot = _catalog_snapshot(session)

    assert snapshot["events"][0]["payload"]["window"]["userActivation"] == {
        "hasBeenActive": True,
        "isActive": False,
    }


def test_extended_rendering_probe_emits_webgpu_evidence(tmp_path) -> None:
    session = SessionState(
        session_id="rendering",
        client_name="owned-browser",
        client_family="test",
        expected_failure=False,
        gate_enabled=False,
    )
    session.probes.append(
        ProbeRecord(
            session_id=session.session_id,
            observed_at="2026-08-05T00:00:00Z",
            realm="deep",
            sequence=1,
            data={
                "extras": {
                    "geometry": {"supported": True, "digest": "abc", "rects": [{"tag": "DIV"}]},
                    "webgpu": {
                        "supported": True,
                        "adapter": True,
                        "features": ["texture-compression-bc"],
                        "limits": {"maxTextureDimension2D": 8192},
                        "digest": "webgpu-shape",
                    },
                    "mediaCapabilities": {"canPlayType": {"video/webm": "probably"}},
                    "runtime": {"preferences": {f"query-{index}": False for index in range(12)}},
                    "cssDefaults": {"button": {"appearance": "auto"}},
                }
            },
        )
    )
    findings, _summary = score_session(session, reputation=_reputation(tmp_path))
    codes = {item.code for item in findings}
    assert "JS_WEBGPU_CAPABILITY_CAPTURED" in codes
    assert "JS_GEOMETRY_PROBE_FAILED" not in codes
