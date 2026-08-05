from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from paritylab.models import JsonValue, json_safe

RUNTIME_PATHS = (
    "runtime.webdriver",
    "runtime.userAgent",
    "runtime.userAgentData.platform",
    "runtime.platform",
    "runtime.vendor",
    "runtime.language",
    "runtime.languages",
    "runtime.hardwareConcurrency",
    "runtime.deviceMemory",
    "runtime.maxTouchPoints",
    "locale.timezone",
    "locale.intlLocale",
    "window.innerWidth",
    "window.innerHeight",
    "window.outerWidth",
    "window.outerHeight",
    "screen.width",
    "screen.height",
    "screen.availWidth",
    "screen.availHeight",
    "screen.colorDepth",
    "screen.pixelDepth",
    "screen.devicePixelRatio",
    "webgl.unmaskedVendor",
    "webgl.unmaskedRenderer",
    "canvas.hash",
    "audio.hash",
    "plugins.pluginCount",
    "plugins.mimeTypeCount",
    "storage.quota",
    "permissions.notifications",
    "notifications.permission",
    "codeIntegrity.functionToStringNative",
    "codeIntegrity.nonNativeExpected",
    "automation.suspiciousGlobals",
    "automation.stackMarkers",
)


def _load(path: Path) -> dict[str, Any]:
    candidate = path / "raw.json" if path.is_dir() else path
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must contain a JSON object: {candidate}")
    return payload


def _deep_get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _realm(document: Mapping[str, Any], realm: str) -> Mapping[str, Any]:
    for probe in document.get("probes", []):
        if isinstance(probe, Mapping) and probe.get("realm") == realm:
            data = probe.get("data")
            if isinstance(data, Mapping):
                return data
    return {}


def _headers(request: Mapping[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for pair in request.get("headers", []):
        if isinstance(pair, list) and len(pair) == 2:
            result.append((str(pair[0]).lower(), str(pair[1])))
    return result


def _first_document(document: Mapping[str, Any]) -> Mapping[str, Any]:
    requests = document.get("requests", [])
    for request in requests:
        if isinstance(request, Mapping) and request.get("path") == "/":
            return request
    return requests[0] if requests and isinstance(requests[0], Mapping) else {}


def summarize_artifact(document: Mapping[str, Any]) -> dict[str, JsonValue]:
    requests = [item for item in document.get("requests", []) if isinstance(item, Mapping)]
    first = _first_document(document)
    headers = _headers(first)
    window = _realm(document, "window")
    final_window = _realm(document, "window-final")
    runtime = {
        path: json_safe(_deep_get(final_window, path) if _deep_get(final_window, path) is not None else _deep_get(window, path))
        for path in RUNTIME_PATHS
    }
    events = [item for item in document.get("behavior", []) if isinstance(item, Mapping)]
    event_types = Counter(str(item.get("event_type", "unknown")) for item in events)
    trusted = [item.get("trusted") for item in events if item.get("trusted") is not None]
    action_times = [
        float(item.get("since_navigation_ms", 0.0))
        for item in events
        if item.get("event_type") in {"pointerdown", "mousedown", "keydown", "click", "scroll"}
    ]
    realms = sorted(
        {
            str(item.get("realm"))
            for item in document.get("probes", [])
            if isinstance(item, Mapping) and item.get("realm")
        }
    )
    tls = [item for item in document.get("tls_fingerprints", []) if isinstance(item, Mapping)]
    opaque = [item for item in document.get("opaque_payloads", []) if isinstance(item, Mapping)]
    return {
        "identity": {
            "session_id": str(document.get("session_id", "")),
            "client_name": str(document.get("client_name", "")),
            "client_family": str(document.get("client_family", "")),
        },
        "network": {
            "http_versions": sorted({str(item.get("http_version")) for item in requests}),
            "request_paths": sorted({str(item.get("path")) for item in requests}),
            "request_count": len(requests),
            "connection_count": len({str(item.get("connection_id")) for item in requests if item.get("connection_id")}),
            "document_header_order": [name for name, _ in headers],
            "document_headers": {name: value for name, value in headers},
        },
        "tls": {
            "ja3": sorted({str(item.get("ja3")) for item in tls if item.get("ja3")}),
            "ja4": sorted({str(item.get("ja4")) for item in tls if item.get("ja4")}),
            "alpn": sorted({protocol for item in tls for protocol in item.get("alpn_protocols", [])}),
            "sni": sorted({str(item.get("server_name")) for item in tls if item.get("server_name")}),
        },
        "runtime": runtime,
        "realms": realms,
        "behavior": {
            "event_count": len(events),
            "event_types": dict(sorted(event_types.items())),
            "trusted_count": sum(value is True for value in trusted),
            "untrusted_count": sum(value is False for value in trusted),
            "first_action_ms": min(action_times) if action_times else None,
        },
        "opaque": {
            "count": len(opaque),
            "content_types": sorted({str(item.get("content_type")) for item in opaque}),
            "entropy": [item.get("shannon_entropy") for item in opaque],
            "sizes": [item.get("body_bytes") for item in opaque],
        },
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, JsonValue]:
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child))
        return result
    safe = json_safe(value)
    return {prefix: safe}


def compare_artifacts(baseline_path: Path, candidate_path: Path) -> dict[str, JsonValue]:
    baseline_summary = summarize_artifact(_load(baseline_path))
    candidate_summary = summarize_artifact(_load(candidate_path))
    baseline_flat = _flatten(baseline_summary)
    candidate_flat = _flatten(candidate_summary)
    differences: list[dict[str, JsonValue]] = []
    for key in sorted(set(baseline_flat) | set(candidate_flat)):
        before = baseline_flat.get(key)
        after = candidate_flat.get(key)
        if before != after:
            differences.append({"key": key, "baseline": before, "candidate": after})
    return {
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "difference_count": len(differences),
        "differences": differences,
    }


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    baseline = comparison.get("baseline", {}).get("identity", {})
    candidate = comparison.get("candidate", {}).get("identity", {})
    lines = [
        "# Browser parity comparison",
        "",
        f"- **Baseline:** `{baseline.get('client_name', '')}` / `{baseline.get('session_id', '')}`",
        f"- **Candidate:** `{candidate.get('client_name', '')}` / `{candidate.get('session_id', '')}`",
        f"- **Differences:** **{comparison.get('difference_count', 0)}**",
        "",
        "This is an exact local artifact diff, not a claim that either fingerprint is universally human or automated.",
        "",
        "| Layer | Key | Baseline | Candidate |",
        "|---|---|---|---|",
    ]
    for item in comparison.get("differences", []):
        key = str(item.get("key", ""))
        layer = key.split(".", 1)[0]
        before = json.dumps(item.get("baseline"), ensure_ascii=False, sort_keys=True)
        after = json.dumps(item.get("candidate"), ensure_ascii=False, sort_keys=True)
        lines.append(f"| {layer} | `{key}` | `{before[:300]}` | `{after[:300]}` |")
    return "\n".join(lines) + "\n"


def write_comparison(baseline: Path, candidate: Path, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison = compare_artifacts(baseline, candidate)
    json_path = output_dir / "comparison.json"
    markdown_path = output_dir / "comparison.md"
    json_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_comparison_markdown(comparison), encoding="utf-8")
    return json_path, markdown_path
