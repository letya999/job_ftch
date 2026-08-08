from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

from paritylab.legacy_snapshot import score_snapshot as score_catalog_snapshot

from paritylab.models import (
    Finding,
    GateDisposition,
    JsonValue,
    ScoreSummary,
    SessionState,
    SignalClass,
)
from paritylab.reputation import OfflineIPReputation
HARD_WEIGHT = 40
MEDIUM_WEIGHT = 15
LOW_WEIGHT = 4

CATALOG_SEVERITY_CLASS = {
    "high": SignalClass.HARD_BOT,
    "medium": SignalClass.MEDIUM,
    "low": SignalClass.LOW,
}

def _finding(
    signal_class: SignalClass,
    code: str,
    title: str,
    reason: str,
    *,
    evidence: Mapping[str, JsonValue] | None = None,
    realms: Iterable[str] = (),
    request_ids: Iterable[str] = (),
) -> Finding:
    weight = {
        SignalClass.HARD_BOT: HARD_WEIGHT,
        SignalClass.MEDIUM: MEDIUM_WEIGHT,
        SignalClass.LOW: LOW_WEIGHT,
        SignalClass.INFO: 0,
    }[signal_class]
    return Finding(
        signal_class=signal_class,
        severity_score=weight,
        code=code,
        title=title,
        reason=reason,
        evidence=dict(evidence or {}),
        realms=tuple(realms),
        request_ids=tuple(request_ids),
    )

def _deep_get(mapping: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current

def _realm_map(session: SessionState) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for probe in sorted(session.probes, key=lambda item: item.sequence):
        output[probe.realm] = dict(probe.data)
    return output

def _header_map(request: Any) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for key, value in request.headers:
        values[key.lower()].append(value)
    return values

def _light_path(path: str) -> str:
    return {
        "/static/probe.js": "/static/app.js",
        "/static/pixel.svg": "/pixel",
        "/api/fetch": "/api/data",
        "/api/beacon": "/api/events",
        "/api/redirect/start": "/redirect",
        "/api/redirect/final": "/api/data",
    }.get(path, path)

def _light_headers(request: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers:
        headers.setdefault(key.lower(), value)
    return headers

def _light_request(request: Any) -> dict[str, Any]:
    path = _light_path(str(request.path))
    query: dict[str, list[str]] = {}
    if request.path == "/api/redirect/final":
        query["phase"] = ["redirect-final"]
    return {
        "seq": request.monotonic_ns,
        "ts": request.monotonic_ns / 1_000_000_000,
        "method": request.method,
        "request_version": f"HTTP/{request.http_version}",
        "path": path,
        "query": query,
        "headers": _light_headers(request),
        "header_order": [key.lower() for key, _ in request.headers],
        "client_host": request.client_host,
        "client_port": request.client_port,
        "body": "",
    }

def _probe_runtime(data: Mapping[str, Any]) -> dict[str, Any]:
    runtime = data.get("runtime") if isinstance(data.get("runtime"), Mapping) else {}
    locale = data.get("locale") if isinstance(data.get("locale"), Mapping) else {}
    webgl = data.get("webgl") if isinstance(data.get("webgl"), Mapping) else {}
    offscreen = data.get("offscreen") if isinstance(data.get("offscreen"), Mapping) else {}
    return {
        "userAgent": runtime.get("userAgent", ""),
        "webdriver": runtime.get("webdriver"),
        "platform": runtime.get("platform", ""),
        "language": runtime.get("language", ""),
        "languages": runtime.get("languages", []),
        "hardwareConcurrency": runtime.get("hardwareConcurrency", 0),
        "deviceMemory": runtime.get("deviceMemory", 0),
        "timezone": locale.get("timezone", ""),
        "webglVendor": webgl.get("unmaskedVendor") or offscreen.get("unmaskedVendor", ""),
        "webglRenderer": webgl.get("unmaskedRenderer") or offscreen.get("unmaskedRenderer", ""),
    }

def _light_user_agent_data(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    output = dict(value)
    if "high" not in output and isinstance(output.get("highEntropy"), Mapping):
        output["high"] = dict(output["highEntropy"])
    return output

def _light_window(data: Mapping[str, Any]) -> dict[str, Any]:
    runtime = data.get("runtime") if isinstance(data.get("runtime"), Mapping) else {}
    plugins = data.get("plugins") if isinstance(data.get("plugins"), Mapping) else {}
    permissions = data.get("permissions") if isinstance(data.get("permissions"), Mapping) else {}
    storage = data.get("storage") if isinstance(data.get("storage"), Mapping) else {}
    media = data.get("media") if isinstance(data.get("media"), Mapping) else {}
    webgl = data.get("webgl") if isinstance(data.get("webgl"), Mapping) else {}
    chrome = data.get("chrome") if isinstance(data.get("chrome"), Mapping) else {}
    code = data.get("codeIntegrity") if isinstance(data.get("codeIntegrity"), Mapping) else {}
    automation = data.get("automation") if isinstance(data.get("automation"), Mapping) else {}
    window = data.get("window") if isinstance(data.get("window"), Mapping) else {}
    screen = data.get("screen") if isinstance(data.get("screen"), Mapping) else {}
    canvas = data.get("canvas") if isinstance(data.get("canvas"), Mapping) else {}
    audio = data.get("audio") if isinstance(data.get("audio"), Mapping) else {}
    fonts = data.get("fonts") if isinstance(data.get("fonts"), Mapping) else {}
    return {
        **_probe_runtime(data),
        "vendor": runtime.get("vendor", ""),
        "cookieEnabled": runtime.get("cookieEnabled"),
        "maxTouchPoints": runtime.get("maxTouchPoints", 0),
        "plugins": plugins.get("names") or plugins.get("items") or [],
        "mimeTypes": plugins.get("mimeTypes") or [],
        "userAgentData": _light_user_agent_data(runtime.get("userAgentData")),
        "permissions": {"supported": True, "values": permissions.get("states") or {}},
        "storage": {
            "localStorage": storage.get("localStorage") is not False,
            "sessionStorage": storage.get("sessionStorage") is not False,
            "indexedDB": storage.get("indexedDB") is not False,
            "estimate": storage.get("estimate") or {},
            "history": storage.get("history") or {},
        },
        "mediaDevices": {
            "supported": media.get("supported", True),
            "count": media.get("deviceCount", media.get("count", 0)),
            "kinds": media.get("deviceKinds", media.get("kinds", [])),
        },
        "deviceAndSensors": data.get("sensors") or data.get("deviceAndSensors") or {},
        "domTripwires": data.get("domTripwires") or {"hidden": False, "hasFocus": True},
        "canvas": {"hash": canvas.get("hash") or canvas.get("dataUrlHash", "")},
        "audio": {"hash": audio.get("hash", ""), "supported": audio.get("supported", True)},
        "fonts": {name: {} for name in fonts.get("available", [])} if isinstance(fonts.get("available"), list) else fonts,
        "chromeShape": {
            "hasChrome": chrome.get("exists", False),
            "hasRuntime": chrome.get("runtimeExists", False),
            "keys": chrome.get("keys", []),
            "runtimeType": "object" if chrome.get("runtimeExists") else "undefined",
        },
        "nativeIntegrity": {
            "functionToStringNative": code.get("functionToStringNative"),
            "fetchNative": not any(
                item == "fetch" for item in code.get("nonNativeExpected", [])
            ),
            "webdriverDescriptor": (code.get("descriptors") or {}).get("navigatorWebdriver"),
            "automationGlobals": automation.get("suspiciousGlobals", []),
            "errorStackSample": code.get("errorStack", ""),
        },
        "userActivation": data.get("userActivation") or {},
        "screen": screen,
        "viewport": {
            "width": window.get("innerWidth", 0),
            "height": window.get("innerHeight", 0),
            "dpr": window.get("devicePixelRatio", 1),
        },
        "webgl": {"extensions": webgl.get("extensions", [])},
        "webglVendor": webgl.get("unmaskedVendor", ""),
        "webglRenderer": webgl.get("unmaskedRenderer", ""),
    }

def _light_interaction(session: SessionState) -> dict[str, Any]:
    interaction: dict[str, Any] = {
        "pointermove": 0,
        "mousemove": 0,
        "click": 0,
        "keydown": 0,
        "scroll": 0,
        "focus": False,
        "pointerTrail": [],
        "mouseTrail": [],
        "keyTrail": [],
    }
    for event in session.behavior:
        if event.event_type in interaction and isinstance(interaction[event.event_type], int):
            interaction[event.event_type] += 1
        if event.event_type == "focus":
            interaction["focus"] = True
        if event.event_type == "pointermove":
            interaction["pointerTrail"].append(
                {"x": event.data.get("x", 0), "y": event.data.get("y", 0)}
            )
        if event.event_type == "mousemove":
            interaction["mouseTrail"].append(
                {"x": event.data.get("x", 0), "y": event.data.get("y", 0)}
            )
        if event.event_type == "keydown":
            interaction["keyTrail"].append({"key": event.data.get("key", "")})
    return interaction

def _catalog_snapshot(session: SessionState) -> dict[str, Any]:
    realms = _realm_map(session)
    window = dict(realms.get("window", {}))
    final_window = realms.get("window-final", {})
    for key in ("userActivation", "focus", "runtime", "locale", "performance"):
        if key in final_window:
            window[key] = final_window[key]
    probe = {
        "kind": "browser_probe",
        "window": _light_window(window),
        "iframe": _light_window(realms["iframe"]) if "iframe" in realms else {},
        "worker": _probe_runtime(realms["classic-worker"]) if "classic-worker" in realms else {},
        "moduleWorker": _probe_runtime(realms["module-worker"]) if "module-worker" in realms else {},
        "serviceWorker": _probe_runtime(realms["service-worker"]) if "service-worker" in realms else {},
        "sharedWorker": _probe_runtime(realms["shared-worker"]) if "shared-worker" in realms else {},
        "interaction": _light_interaction(session),
        "performance": realms.get("network-client", {}).get(
            "performance", window.get("performance", {})
        ),
    }
    return {
        "requests": [_light_request(request) for request in session.requests],
        "events": [{"payload": probe}] if window else [],
    }
