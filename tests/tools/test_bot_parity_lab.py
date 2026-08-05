from __future__ import annotations

import json

from tools.bot_parity_lab.scoring import score_snapshot, to_markdown


def _request(
    path: str,
    headers: dict[str, str] | None = None,
    query: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        "seq": 1,
        "ts": 1.0,
        "method": "GET",
        "request_version": "HTTP/1.1",
        "path": path,
        "query": query or {},
        "headers": {
            "host": "127.0.0.1",
            "connection": "keep-alive",
            "accept": "text/html",
            "accept-language": "en-US,en;q=0.9",
            "accept-encoding": "gzip, deflate, br",
            "user-agent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
            "sec-fetch-site": "none",
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
            "sec-ch-ua": '"Chromium";v="145"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            **(headers or {}),
        },
        "header_order": ["host", "connection", "accept", "user-agent"],
        "client_host": "127.0.0.1",
        "client_port": 51000,
        "body": "",
    }


def _browser_probe() -> dict[str, object]:
    window = {
        "userAgent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
        "webdriver": None,
        "platform": "Win32",
        "language": "en-US",
        "languages": ["en-US", "en"],
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "timezone": "America/New_York",
        "vendor": "Google Inc.",
        "cookieEnabled": True,
        "maxTouchPoints": 0,
        "plugins": ["PDF Viewer"],
        "mimeTypes": ["application/pdf"],
        "userAgentData": {
            "brands": [{"brand": "Chromium", "version": "145"}],
            "platform": "Windows",
            "mobile": False,
            "high": {
                "architecture": "x86",
                "bitness": "64",
                "brands": [{"brand": "Chromium", "version": "145"}],
                "fullVersionList": [{"brand": "Chromium", "version": "145.0.0.0"}],
                "mobile": False,
                "model": "",
                "platform": "Windows",
                "platformVersion": "16.0.0",
                "uaFullVersion": "145.0.0.0",
                "wow64": False,
            },
        },
        "permissions": {
            "supported": True,
            "values": {
                "notifications": "prompt",
                "clipboard-read": "prompt",
                "clipboard-write": "granted",
                "geolocation": "denied",
                "camera": "denied",
                "microphone": "denied",
            },
        },
        "storage": {
            "localStorage": True,
            "sessionStorage": True,
            "indexedDB": True,
            "estimate": {"quota": 10_000_000_000, "usage": 42},
            "history": {"sidHash": "abc123", "visits": 2},
        },
        "mediaDevices": {
            "supported": True,
            "count": 3,
            "kinds": ["audioinput", "audiooutput", "videoinput"],
        },
        "deviceAndSensors": {
            "touchEvent": False,
            "coarsePointer": False,
            "finePointer": True,
            "connection": {"rtt": 50, "downlink": 10},
        },
        "domTripwires": {
            "decoyValueLength": 0,
            "hidden": False,
            "hasFocus": True,
            "captchaTokenPresent": False,
        },
        "canvas": {"hash": "canvas", "length": 1000},
        "audio": {"hash": "audio", "supported": True},
        "fonts": {
            "Arial": {},
            "Times New Roman": {},
            "Segoe UI": {},
            "Roboto": {},
            "Apple Color Emoji": {},
            "Noto Color Emoji": {},
            "LabProbe": {},
        },
        "chromeShape": {
            "hasChrome": True,
            "hasRuntime": False,
            "keys": ["app"],
            "runtimeType": "undefined",
        },
        "nativeIntegrity": {
            "functionToStringNative": True,
            "fetchNative": True,
            "webdriverDescriptor": {"hasGetter": True},
            "automationGlobals": [],
            "errorStackSample": "Error: lab_stack_probe",
        },
        "userActivation": {"isActive": True, "hasBeenActive": True},
        "screen": {"width": 1280, "height": 720},
        "viewport": {"width": 1280, "height": 720, "dpr": 1},
        "webglVendor": "Google Inc. (Intel)",
        "webglRenderer": "ANGLE (Intel, Intel UHD Graphics Direct3D11)",
        "webgl": {"extensions": [f"EXT_{idx}" for idx in range(12)]},
    }
    worker = {
        key: window[key]
        for key in (
            "userAgent",
            "platform",
            "language",
            "languages",
            "hardwareConcurrency",
            "deviceMemory",
            "timezone",
            "webglVendor",
            "webglRenderer",
        )
    }
    service_worker = {
        key: window[key]
        for key in (
            "userAgent",
            "platform",
            "language",
            "languages",
            "hardwareConcurrency",
            "deviceMemory",
            "timezone",
        )
    }
    return {
        "kind": "browser_probe",
        "window": window,
        "iframe": {
            key: window[key]
            for key in (
                "userAgent",
                "platform",
                "language",
                "languages",
                "hardwareConcurrency",
                "deviceMemory",
                "timezone",
                "vendor",
            )
        },
        "worker": worker,
        "moduleWorker": worker,
        "serviceWorker": service_worker,
        "sharedWorker": service_worker,
        "interaction": {
            "pointermove": 2,
            "mousemove": 2,
            "click": 1,
            "keydown": 1,
            "scroll": 1,
            "focus": True,
            "pointerTrail": [{"x": 10, "y": 10}, {"x": 25, "y": 18}, {"x": 31, "y": 45}],
            "mouseTrail": [{"x": 10, "y": 10}, {"x": 25, "y": 18}, {"x": 31, "y": 45}],
            "keyTrail": [{"key": "Tab"}],
        },
        "performance": {
            "resourceCount": 6,
            "resources": [
                {
                    "name": "/static/app.js",
                    "initiatorType": "link",
                    "duration": 1,
                    "transferSize": 100,
                },
                {"name": "/pixel", "initiatorType": "img", "duration": 1, "transferSize": 100},
                {
                    "name": "/static/lab.woff2",
                    "initiatorType": "css",
                    "duration": 1,
                    "transferSize": 100,
                },
                {"name": "/api/data", "initiatorType": "fetch", "duration": 1, "transferSize": 100},
                {
                    "name": "/favicon.ico",
                    "initiatorType": "other",
                    "duration": 1,
                    "transferSize": 100,
                },
            ],
        },
    }


def test_raw_http_negative_control_is_detected() -> None:
    snapshot = {
        "requests": [_request("/", {"user-agent": "python-httpx/0.28.1", "accept": "*/*"})],
        "events": [],
    }

    report = score_snapshot("httpx_raw", snapshot)

    codes = {finding.code for finding in report.findings}
    assert not report.ok
    assert "RAW_CLIENT_UA" in codes
    assert "NO_JS_BEACON" in codes
    assert report.signal_count >= 100


def test_deep_catalog_detects_service_worker_realm_leak() -> None:
    probe = _browser_probe()
    probe["serviceWorker"]["userAgent"] = "Mozilla/5.0 HeadlessChrome/150.0.0.0"  # type: ignore[index]
    snapshot = {
        "requests": [
            _request("/"),
            _request("/static/app.js", {"sec-fetch-dest": "script"}),
            _request("/sw.js", {"sec-fetch-dest": "serviceworker"}),
            _request("/shared-worker.js", {"sec-fetch-dest": "sharedworker"}),
            _request("/api/data", {"sec-fetch-dest": "empty"}, {"phase": ["warm"]}),
            _request("/api/echo-headers", {"sec-fetch-dest": "empty"}),
            _request("/captcha/challenge", {"sec-fetch-dest": "empty"}),
            _request("/api/events", {"sec-fetch-dest": "empty"}),
            _request("/pixel", {"sec-fetch-dest": "image"}),
            _request("/favicon.ico", {"sec-fetch-dest": "image"}),
            _request("/static/style.css", {"sec-fetch-dest": "style"}),
            _request("/static/lab.woff2", {"sec-fetch-dest": "font"}),
            _request("/redirect", {"sec-fetch-dest": "empty"}),
            _request("/api/data", {"sec-fetch-dest": "empty"}, {"phase": ["redirect-final"]}),
        ],
        "events": [{"payload": probe}],
    }

    report = score_snapshot("patchright_browser", snapshot)

    codes = {finding.code for finding in report.findings}
    assert not report.ok
    assert "CAT_REALM_AXIS_MISMATCH_service_worker_userAgent" in codes
    assert report.signal_count >= 600


def test_deep_catalog_accepts_anonymized_session_history_marker() -> None:
    probe = _browser_probe()
    probe["window"]["storage"]["history"] = {"sessionPresent": True, "visits": 1}  # type: ignore[index]
    snapshot = {"requests": [_request("/")], "events": [{"payload": probe}]}

    report = score_snapshot("patchright_browser", snapshot)

    assert "CAT_HISTORY_ID_MISSING" not in {finding.code for finding in report.findings}


def test_deep_catalog_accepts_firefox_angle_on_windows() -> None:
    probe = _browser_probe()
    window = probe["window"]  # type: ignore[index]
    window["userAgent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
    )
    window["userAgentData"] = None
    window["chromeShape"] = {"hasChrome": False, "hasRuntime": False}
    snapshot = {"requests": [_request("/")], "events": [{"payload": probe}]}

    report = score_snapshot("camoufox", snapshot)

    assert "CAT_FIREFOX_ANGLE_WEBGL" not in {finding.code for finding in report.findings}


def test_markdown_and_json_contract_include_signal_count() -> None:
    report = score_snapshot("httpx_raw", {"requests": [_request("/")], "events": []})

    payload = {
        "client": report.client,
        "score": report.score,
        "ok": report.ok,
        "signal_count": report.signal_count,
    }
    markdown = to_markdown([report])

    assert json.loads(json.dumps(payload))["signal_count"] == report.signal_count
    assert "| client | verdict | score | signals | requests | events |" in markdown
