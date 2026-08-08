from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

NETWORK_PATHS = {
    "/",
    "/static/app.js",
    "/static/style.css",
    "/static/lab.woff2",
    "/favicon.ico",
    "/api/data",
    "/api/echo-headers",
    "/api/events",
    "/captcha/challenge",
    "/redirect",
    "/shared-worker.js",
    "/sw.js",
    "/pixel",
}
WORKER_AXES = (
    "userAgent",
    "platform",
    "language",
    "timezone",
    "hardwareConcurrency",
    "deviceMemory",
    "webglVendor",
    "webglRenderer",
)
IFRAME_AXES = (
    "userAgent",
    "platform",
    "language",
    "timezone",
    "hardwareConcurrency",
    "deviceMemory",
    "vendor",
)
AUTOMATION_STACK_TOKENS = ("playwright", "puppeteer", "selenium", "webdriver", "cdc_")
COMMON_HEADERS = (
    "host",
    "connection",
    "accept",
    "accept-language",
    "accept-encoding",
    "user-agent",
    "sec-fetch-site",
    "sec-fetch-mode",
    "sec-fetch-dest",
    "sec-fetch-user",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "upgrade-insecure-requests",
    "cache-control",
    "pragma",
)
RUNTIME_FIELDS = (
    "userAgent",
    "webdriver",
    "platform",
    "language",
    "languages",
    "hardwareConcurrency",
    "deviceMemory",
    "timezone",
    "vendor",
    "cookieEnabled",
    "maxTouchPoints",
    "plugins",
    "mimeTypes",
    "userAgentData",
    "permissions",
    "storage",
    "mediaDevices",
    "deviceAndSensors",
    "domTripwires",
    "canvas",
    "audio",
    "fonts",
    "chromeShape",
    "nativeIntegrity",
    "userActivation",
    "screen",
    "viewport",
    "webgl",
)
PERMISSION_NAMES = (
    "notifications",
    "clipboard-read",
    "clipboard-write",
    "geolocation",
    "camera",
    "microphone",
)
CH_HIGH_FIELDS = (
    "architecture",
    "bitness",
    "brands",
    "fullVersionList",
    "mobile",
    "model",
    "platform",
    "platformVersion",
    "uaFullVersion",
    "wow64",
)
BEHAVIOR_FIELDS = ("pointermove", "mousemove", "click", "keydown", "scroll", "focus")
EXTENDED_REALM_AXES = (
    "userAgent",
    "platform",
    "language",
    "timezone",
    "hardwareConcurrency",
    "deviceMemory",
)


@dataclass(slots=True)
class Finding:
    code: str
    severity: str
    detail: str


@dataclass(slots=True)
class ScoreReport:
    client: str
    findings: list[Finding] = field(default_factory=list)
    request_count: int = 0
    event_count: int = 0
    signal_count: int = 0

    @property
    def ok(self) -> bool:
        return not any(f.severity in {"high", "medium"} for f in self.findings)

    @property
    def score(self) -> int:
        weights = {"high": 40, "medium": 15, "low": 5}
        return sum(weights.get(f.severity, 1) for f in self.findings)


def _chrome_major_from_ua(ua: str) -> str:
    match = re.search(r"Chrome/(\d+)", ua)
    return match.group(1) if match else ""


def _version_like_full(value: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value or ""))


def _latest_browser_probe(events: list[dict[str, Any]]) -> dict[str, Any]:
    probes = [
        event.get("payload") or {}
        for event in events
        if (event.get("payload") or {}).get("kind") == "browser_probe"
    ]
    return probes[-1] if probes else {}


def _has_fetch_resource(probe: dict[str, Any]) -> bool:
    resources = (probe.get("performance") or {}).get("resources") or []
    return any(resource.get("initiatorType") == "fetch" for resource in resources)


def _client_host_class(host: str) -> str:
    if host in {"127.0.0.1", "::1", "localhost"}:
        return "loopback"
    if host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.")):
        return "private"
    return "public_or_unknown"


def _trail_stats(points: list[dict[str, Any]]) -> dict[str, float]:
    if len(points) < 2:
        return {
            "count": float(len(points)),
            "distance": 0.0,
            "straightness": 1.0,
            "unique": float(len(points)),
        }
    distance = 0.0
    unique = {(int(point.get("x") or 0), int(point.get("y") or 0)) for point in points}
    for prev, current in pairwise(points):
        dx = float(current.get("x") or 0) - float(prev.get("x") or 0)
        dy = float(current.get("y") or 0) - float(prev.get("y") or 0)
        distance += (dx * dx + dy * dy) ** 0.5
    dx_total = float(points[-1].get("x") or 0) - float(points[0].get("x") or 0)
    dy_total = float(points[-1].get("y") or 0) - float(points[0].get("y") or 0)
    direct = (dx_total * dx_total + dy_total * dy_total) ** 0.5
    return {
        "count": float(len(points)),
        "distance": distance,
        "straightness": direct / distance if distance else 1.0,
        "unique": float(len(unique)),
    }


def _add_deep_catalog_findings(
    report: ScoreReport,
    *,
    requests: list[dict[str, Any]],
    events: list[dict[str, Any]],
    paths: list[str],
    first_headers: dict[str, str],
    probe: dict[str, Any],
) -> None:
    """Evaluate a broad defensive catalog of atomic browser-bot signals."""

    def check(condition: bool, code: str, severity: str, detail: str) -> None:
        report.signal_count += 1
        if condition:
            report.findings.append(Finding(code, severity, detail))

    ua = first_headers.get("user-agent", "")
    ua_lower = ua.lower()
    is_chrome = "chrome/" in ua_lower and "firefox/" not in ua_lower
    is_firefox = "firefox/" in ua_lower

    for expected_path in sorted(NETWORK_PATHS):
        check(
            expected_path not in paths,
            f"CAT_PATH_MISSING_{expected_path.strip('/').replace('/', '_') or 'root'}",
            "low",
            f"{expected_path} missing from waterfall",
        )

    for req in requests:
        headers = req.get("headers") or {}
        path = str(req.get("path") or "")
        for header in COMMON_HEADERS:
            optional = header in {
                "sec-fetch-user",
                "cache-control",
                "pragma",
                "upgrade-insecure-requests",
            }
            optional = optional or (header.startswith("sec-ch-ua") and not is_chrome)
            check(
                not optional and header not in headers,
                f"CAT_HEADER_MISSING_{path.strip('/').replace('/', '_') or 'root'}_{header.replace('-', '_')}",
                "low",
                f"{path} request lacks {header}",
            )
        if path == "/":
            check(
                headers.get("sec-fetch-dest") != "document",
                "CAT_NAV_DEST_NOT_DOCUMENT",
                "medium",
                f"navigation dest={headers.get('sec-fetch-dest')!r}",
            )
            check(
                headers.get("sec-fetch-mode") != "navigate",
                "CAT_NAV_MODE_NOT_NAVIGATE",
                "medium",
                f"navigation mode={headers.get('sec-fetch-mode')!r}",
            )
        if path.endswith(".js"):
            expected_script_dests = {"script", "serviceworker", "sharedworker", "worker"}
            check(
                headers.get("sec-fetch-dest") not in expected_script_dests,
                "CAT_SCRIPT_DEST_MISMATCH",
                "medium",
                f"script dest={headers.get('sec-fetch-dest')!r}",
            )
        if path.endswith(".css"):
            check(
                headers.get("sec-fetch-dest") != "style",
                "CAT_STYLE_DEST_MISMATCH",
                "medium",
                f"style dest={headers.get('sec-fetch-dest')!r}",
            )
        if path in {"/pixel", "/favicon.ico"}:
            check(
                headers.get("sec-fetch-dest") != "image",
                "CAT_IMAGE_DEST_MISMATCH",
                "low",
                f"{path} dest={headers.get('sec-fetch-dest')!r}",
            )
        client_host = str(req.get("client_host") or "")
        request_label = path.strip("/").replace("/", "_") or "root"
        request_version = str(req.get("request_version") or "")
        check(
            request_version not in {"HTTP/1.1", "HTTP/2", "HTTP/3"},
            f"CAT_HTTP_VERSION_ODD_{request_label}",
            "low",
            f"{path} request_version={request_version!r}",
        )
        check(
            request_version == "HTTP/1.0",
            f"CAT_HTTP10_REQUEST_{request_label}",
            "medium",
            f"{path} used HTTP/1.0",
        )
        check(
            not client_host,
            f"CAT_CLIENT_IP_MISSING_{request_label}",
            "medium",
            f"{path} request has no client host",
        )
        check(
            bool(client_host) and _client_host_class(client_host) == "public_or_unknown",
            f"CAT_CLIENT_IP_PUBLIC_OR_UNKNOWN_{request_label}",
            "low",
            f"{path} client host={client_host!r}",
        )

    if not probe:
        report.signal_count += 90
        return

    window = probe.get("window") or {}
    iframe = probe.get("iframe") or {}
    worker = probe.get("worker") or {}
    module_worker = probe.get("moduleWorker") or {}
    service_worker = probe.get("serviceWorker") or {}
    shared_worker = probe.get("sharedWorker") or {}
    realms = {
        "window": window,
        "iframe": iframe,
        "worker": worker,
        "module_worker": module_worker,
        "service_worker": service_worker,
        "shared_worker": shared_worker,
    }

    for realm_name, realm in realms.items():
        for field_name in RUNTIME_FIELDS:
            if realm_name in {"worker", "module_worker"} and field_name not in WORKER_AXES:
                report.signal_count += 1
                continue
            if (
                realm_name in {"service_worker", "shared_worker"}
                and field_name not in EXTENDED_REALM_AXES
            ):
                report.signal_count += 1
                continue
            if realm_name == "iframe" and field_name not in IFRAME_AXES:
                report.signal_count += 1
                continue
            check(
                isinstance(realm, dict) and not realm.get("error") and field_name not in realm,
                f"CAT_RUNTIME_FIELD_MISSING_{realm_name}_{field_name}",
                "low",
                f"{realm_name}.{field_name} is missing",
            )

    for axis in WORKER_AXES:
        for realm_name, realm in (
            ("iframe", iframe),
            ("worker", worker),
            ("module_worker", module_worker),
            ("service_worker", service_worker),
            ("shared_worker", shared_worker),
        ):
            if not isinstance(realm, dict) or realm.get("error"):
                report.signal_count += 1
                continue
            if axis in realm:
                check(
                    window.get(axis) != realm.get(axis),
                    f"CAT_REALM_AXIS_MISMATCH_{realm_name}_{axis}",
                    "high",
                    f"{realm_name}.{axis}: {realm.get(axis)!r} != window {window.get(axis)!r}",
                )
            else:
                report.signal_count += 1
    for realm_name, realm in (("service_worker", service_worker), ("shared_worker", shared_worker)):
        check(
            isinstance(realm, dict) and bool(realm.get("error")),
            f"CAT_{realm_name.upper()}_ERROR",
            "medium",
            f"{realm_name} probe failed: {realm!r}",
        )
        check(
            isinstance(realm, dict) and realm.get("supported") is False,
            f"CAT_{realm_name.upper()}_UNSUPPORTED",
            "low",
            f"{realm_name} unsupported",
        )

    native = window.get("nativeIntegrity") or {}
    webdriver_desc = native.get("webdriverDescriptor")
    stack_sample = str(native.get("errorStackSample") or "")
    check(
        is_chrome and webdriver_desc is None,
        "CAT_CHROME_WEBDRIVER_DESCRIPTOR_MISSING",
        "medium",
        "Chrome-like runtime has no Navigator.prototype.webdriver descriptor",
    )
    check(
        "new window.Error" in stack_sample,
        "CAT_ERROR_CONSTRUCTOR_WRAPPED",
        "medium",
        "Error stack shows wrapped window.Error constructor",
    )
    check(
        "<anonymous>:" in stack_sample and "new window.Error" in stack_sample,
        "CAT_INIT_SCRIPT_STACK_FRAME",
        "medium",
        "stack includes anonymous init-script frame",
    )
    check(
        isinstance(webdriver_desc, dict) and webdriver_desc.get("hasGetter") is False,
        "CAT_WEBDRIVER_DESCRIPTOR_NO_GETTER",
        "medium",
        f"webdriver descriptor={webdriver_desc!r}",
    )

    chrome_shape = window.get("chromeShape") or {}
    check(
        is_chrome and not chrome_shape.get("hasChrome"),
        "CAT_CHROME_OBJECT_MISSING",
        "medium",
        "Chrome UA lacks window.chrome",
    )
    check(
        is_chrome and chrome_shape.get("hasRuntime"),
        "CAT_CHROME_RUNTIME_SYNTHETIC",
        "medium",
        "window.chrome.runtime object is present on a plain web page",
    )
    check(
        is_firefox and chrome_shape.get("hasChrome"),
        "CAT_FIREFOX_HAS_CHROME_OBJECT",
        "high",
        "Firefox UA exposes window.chrome",
    )

    ua_data = window.get("userAgentData")
    chrome_major = _chrome_major_from_ua(ua)
    if isinstance(ua_data, dict):
        high = ua_data.get("high") or {}
        brands = list(ua_data.get("brands") or [])
        high_brands = list(high.get("brands") or [])
        brand_versions = {
            str(item.get("brand")): str(item.get("version"))
            for item in brands
            if isinstance(item, dict)
        }
        high_brand_versions = {
            str(item.get("brand")): str(item.get("version"))
            for item in high_brands
            if isinstance(item, dict)
        }
        for field in CH_HIGH_FIELDS:
            check(
                field not in high,
                f"CAT_UACH_HIGH_FIELD_MISSING_{field}",
                "low",
                f"UA-CH high entropy missing {field}",
            )
        check(
            is_chrome and chrome_major and chrome_major not in brand_versions.values(),
            "CAT_UACH_BRAND_VERSION_DRIFT",
            "high",
            f"Chrome UA major {chrome_major} not in UA-CH brands {brand_versions!r}",
        )
        check(
            is_chrome and chrome_major and chrome_major not in high_brand_versions.values(),
            "CAT_UACH_HIGH_BRAND_VERSION_DRIFT",
            "high",
            f"Chrome UA major {chrome_major} not in high entropy brands {high_brand_versions!r}",
        )
        check(
            is_chrome
            and high.get("uaFullVersion")
            and not _version_like_full(str(high.get("uaFullVersion"))),
            "CAT_UACH_FULL_VERSION_TRUNCATED",
            "medium",
            f"uaFullVersion={high.get('uaFullVersion')!r}",
        )
        check(
            is_chrome and not high.get("fullVersionList"),
            "CAT_UACH_FULL_VERSION_LIST_EMPTY",
            "medium",
            "fullVersionList is empty",
        )
        check(
            is_chrome and not high.get("architecture"),
            "CAT_UACH_ARCH_EMPTY",
            "medium",
            "architecture is empty",
        )
        check(
            is_chrome and not high.get("bitness"),
            "CAT_UACH_BITNESS_EMPTY",
            "medium",
            "bitness is empty",
        )
    else:
        for _field in CH_HIGH_FIELDS:
            report.signal_count += 1
        check(
            is_chrome,
            "CAT_UACH_API_MISSING_FOR_CHROME",
            "medium",
            "Chrome UA lacks navigator.userAgentData",
        )
        check(
            is_firefox and ua_data is not None,
            "CAT_UACH_PRESENT_FOR_FIREFOX",
            "medium",
            "Firefox exposes UA Client Hints",
        )

    permissions = (window.get("permissions") or {}).get("values") or {}
    for name in PERMISSION_NAMES:
        value = str(permissions.get(name) or "")
        check(
            not value,
            f"CAT_PERMISSION_MISSING_{name.replace('-', '_')}",
            "low",
            f"permission {name} missing",
        )
        check(
            value.startswith("error:"),
            f"CAT_PERMISSION_ERROR_{name.replace('-', '_')}",
            "low",
            f"permission {name} returned {value!r}",
        )
    check(
        is_chrome and permissions.get("notifications") == "default",
        "CAT_NOTIFICATION_DEFAULT_CHROME",
        "low",
        "Chrome notification permission reports default instead of prompt",
    )

    storage = window.get("storage") or {}
    estimate = storage.get("estimate") or {}
    history = storage.get("history") or {}
    check(
        estimate.get("usage") == 1234567,
        "CAT_STORAGE_MAGIC_USAGE",
        "medium",
        "storage usage is synthetic magic value 1234567",
    )
    check(
        isinstance(estimate.get("quota"), int) and estimate.get("quota", 0) < 1_000_000_000,
        "CAT_STORAGE_QUOTA_SMALL",
        "low",
        f"storage quota={estimate.get('quota')!r}",
    )
    check(
        storage.get("localStorage") is not True,
        "CAT_LOCAL_STORAGE_FALSE",
        "medium",
        "localStorage unavailable",
    )
    check(
        storage.get("sessionStorage") is not True,
        "CAT_SESSION_STORAGE_FALSE",
        "medium",
        "sessionStorage unavailable",
    )
    check(
        storage.get("indexedDB") is not True,
        "CAT_INDEXEDDB_FALSE",
        "medium",
        "indexedDB unavailable",
    )
    has_session_history = bool(history.get("sidHash") or history.get("sessionPresent"))
    check(
        not has_session_history,
        "CAT_HISTORY_ID_MISSING",
        "medium",
        "local session-history marker missing",
    )
    check(
        isinstance(history.get("visits"), int) and history.get("visits", 0) < 1,
        "CAT_HISTORY_VISIT_COUNT_ZERO",
        "low",
        f"history visits={history.get('visits')!r}",
    )

    media = window.get("mediaDevices") or {}
    kinds = media.get("kinds") or []
    check(
        is_chrome and int(media.get("count") or 0) < 3,
        "CAT_CHROME_MEDIA_DEVICE_COUNT_LOW",
        "low",
        f"media device count={media.get('count')!r}",
    )
    for kind in ("audioinput", "audiooutput", "videoinput"):
        check(
            kind not in kinds and is_chrome,
            f"CAT_MEDIA_KIND_MISSING_{kind}",
            "low",
            f"{kind} missing",
        )

    webgl = window.get("webgl") or {}
    extensions = webgl.get("extensions") or []
    check(
        is_chrome and len(extensions) < 10,
        "CAT_WEBGL_EXTENSION_COUNT_LOW",
        "low",
        f"WebGL extensions={len(extensions)}",
    )
    check(
        "SwiftShader" in str(window.get("webglRenderer") or ""),
        "CAT_WEBGL_SWIFTSHADER",
        "high",
        f"renderer={window.get('webglRenderer')!r}",
    )
    check(
        is_chrome and "Apple" in str(window.get("webglRenderer") or ""),
        "CAT_CHROME_APPLE_WEBGL",
        "medium",
        f"renderer={window.get('webglRenderer')!r}",
    )
    check(
        "Macintosh; Intel Mac OS X" in ua and "Apple M1" in str(window.get("webglRenderer") or ""),
        "CAT_INTEL_MAC_APPLE_SILICON_WEBGL",
        "medium",
        f"UA={ua!r}, renderer={window.get('webglRenderer')!r}",
    )
    check(
        is_firefox
        and "ANGLE" in str(window.get("webglRenderer") or "")
        and not str(window.get("platform") or "").startswith("Win"),
        "CAT_FIREFOX_ANGLE_WEBGL",
        "medium",
        f"platform={window.get('platform')!r}, renderer={window.get('webglRenderer')!r}",
    )

    canvas = window.get("canvas") or {}
    audio = window.get("audio") or {}
    fonts = window.get("fonts") or {}
    check(not canvas.get("hash"), "CAT_CANVAS_HASH_EMPTY", "medium", "canvas hash empty")
    check(not audio.get("hash"), "CAT_AUDIO_HASH_EMPTY", "low", "audio hash empty")
    for font in (
        "Arial",
        "Times New Roman",
        "Segoe UI",
        "Roboto",
        "Apple Color Emoji",
        "Noto Color Emoji",
        "LabProbe",
    ):
        check(
            font not in fonts,
            f"CAT_FONT_METRIC_MISSING_{re.sub(r'[^A-Za-z0-9]+', '_', font).strip('_')}",
            "low",
            f"{font} metrics missing",
        )

    interaction = probe.get("interaction") or {}
    for field in BEHAVIOR_FIELDS:
        check(
            field not in interaction,
            f"CAT_BEHAVIOR_FIELD_MISSING_{field}",
            "low",
            f"interaction.{field} missing",
        )
    check(
        interaction.get("click", 0) and not interaction.get("focus"),
        "CAT_CLICK_WITHOUT_FOCUS",
        "low",
        "click happened while document.hasFocus() was false",
    )
    check(
        interaction.get("click", 0) and not interaction.get("keydown"),
        "CAT_CLICK_WITHOUT_KEYBOARD",
        "low",
        "click path had no keyboard event",
    )
    check(
        interaction.get("click", 0) and not interaction.get("pointermove"),
        "CAT_CLICK_WITHOUT_POINTERMOVE",
        "medium",
        "click path had no pointermove",
    )
    check(
        interaction.get("click", 0) and not interaction.get("scroll"),
        "CAT_CLICK_WITHOUT_SCROLL",
        "low",
        "click path had no scroll",
    )
    pointer_stats = _trail_stats(interaction.get("pointerTrail") or [])
    mouse_stats = _trail_stats(interaction.get("mouseTrail") or [])
    check(
        interaction.get("click", 0) and pointer_stats["unique"] < 2,
        "CAT_POINTER_TRAIL_LOW_UNIQUE_POINTS",
        "medium",
        f"pointer trail stats={pointer_stats!r}",
    )
    check(
        pointer_stats["count"] >= 2 and pointer_stats["straightness"] > 0.98,
        "CAT_POINTER_TRAIL_TOO_STRAIGHT",
        "low",
        f"pointer trail stats={pointer_stats!r}",
    )
    check(
        mouse_stats["count"] >= 2 and mouse_stats["straightness"] > 0.98,
        "CAT_MOUSE_TRAIL_TOO_STRAIGHT",
        "low",
        f"mouse trail stats={mouse_stats!r}",
    )
    check(
        interaction.get("click", 0) and len(interaction.get("keyTrail") or []) == 0,
        "CAT_KEY_TRAIL_EMPTY_AFTER_CLICK",
        "low",
        "no key trail entries after click path",
    )

    device = window.get("deviceAndSensors") or {}
    check(
        int(window.get("maxTouchPoints") or 0) > 0 and not device.get("touchEvent"),
        "CAT_TOUCH_POINTS_WITHOUT_TOUCHEVENT",
        "medium",
        f"maxTouchPoints={window.get('maxTouchPoints')!r}, touchEvent={device.get('touchEvent')!r}",
    )
    check(
        int(window.get("maxTouchPoints") or 0) == 0 and device.get("coarsePointer"),
        "CAT_COARSE_POINTER_WITHOUT_TOUCH",
        "medium",
        "coarse pointer but maxTouchPoints=0",
    )
    check(
        bool(device.get("coarsePointer")) and bool(device.get("finePointer")),
        "CAT_POINTER_MEDIA_CONFLICT",
        "low",
        f"device pointer media={device!r}",
    )
    connection = device.get("connection") or {}
    check(
        bool(connection) and connection.get("rtt") == 0 and connection.get("downlink") == 0,
        "CAT_NETWORK_INFORMATION_ZEROED",
        "low",
        f"navigator.connection={connection!r}",
    )

    dom = window.get("domTripwires") or {}
    check(
        int(dom.get("decoyValueLength") or 0) > 0,
        "CAT_HONEYPOT_FIELD_FILLED",
        "high",
        f"hidden honeypot length={dom.get('decoyValueLength')!r}",
    )
    check(
        dom.get("hidden") is True,
        "CAT_PAGE_HIDDEN_DURING_INTERACTION",
        "medium",
        "document.hidden=true",
    )
    check(
        interaction.get("click", 0) and dom.get("hasFocus") is False,
        "CAT_DOM_CLICK_WITHOUT_FOCUS",
        "low",
        f"dom tripwire={dom!r}",
    )

    browser_probes = [
        event.get("payload") or {}
        for event in events
        if (event.get("payload") or {}).get("kind") == "browser_probe"
    ]
    if len(browser_probes) >= 2:
        first = browser_probes[0].get("window") or {}
        last = browser_probes[-1].get("window") or {}
        for axis in ("userAgent", "platform", "language", "timezone", "webglRenderer"):
            check(
                first.get(axis) != last.get(axis),
                f"CAT_TEMPORAL_IDENTITY_DRIFT_{axis}",
                "high",
                f"{axis}: first={first.get(axis)!r}, last={last.get(axis)!r}",
            )
        first_history = (first.get("storage") or {}).get("history") or {}
        last_history = (last.get("storage") or {}).get("history") or {}
        check(
            first_history.get("sidHash") != last_history.get("sidHash"),
            "CAT_TEMPORAL_HISTORY_ID_DRIFT",
            "high",
            f"sid first={first_history.get('sidHash')!r}, last={last_history.get('sidHash')!r}",
        )
    else:
        report.signal_count += 6

    performance = probe.get("performance") or {}
    resources = performance.get("resources") or []
    request_has_fetches = any(
        path in {"/api/data", "/api/echo-headers", "/redirect"} for path in paths
    )
    check(
        request_has_fetches and not _has_fetch_resource(probe),
        "CAT_FETCH_REQUESTS_MISSING_FROM_RESOURCE_TIMING",
        "medium",
        "server saw fetches but Resource Timing did not expose fetch entries",
    )
    for initiator in ("link", "img", "css", "fetch", "other"):
        check(
            not any(resource.get("initiatorType") == initiator for resource in resources),
            f"CAT_RESOURCE_INITIATOR_MISSING_{initiator}",
            "low",
            f"no PerformanceResourceTiming initiatorType={initiator}",
        )
    for resource in resources:
        name = str(resource.get("name") or "resource")
        check(
            float(resource.get("duration") or 0) <= 0,
            f"CAT_RESOURCE_DURATION_ZERO_{hash(name) & 0xFFFF:x}",
            "low",
            f"{name} duration={resource.get('duration')!r}",
        )
        check(
            int(resource.get("transferSize") or 0) == 0 and not name.endswith("/favicon.ico"),
            f"CAT_RESOURCE_TRANSFER_ZERO_{hash(name) & 0xFFFF:x}",
            "low",
            f"{name} transferSize={resource.get('transferSize')!r}",
        )


def score_snapshot(client: str, snapshot: dict[str, Any]) -> ScoreReport:
    requests = list(snapshot.get("requests") or [])
    events = list(snapshot.get("events") or [])
    report = ScoreReport(client=client, request_count=len(requests), event_count=len(events))
    paths = [str(req.get("path", "")) for req in requests]
    headers = [req.get("headers") or {} for req in requests]
    first_headers = headers[0] if headers else {}
    probe = _latest_browser_probe(events)

    if not requests:
        report.findings.append(Finding("NO_REQUESTS", "high", "collector saw no requests"))
        return report
    if not events:
        report.findings.append(Finding("NO_JS_BEACON", "high", "page JS did not post /api/events"))
    if "/static/app.js" not in paths:
        report.findings.append(Finding("NO_SCRIPT_FETCH", "medium", "script asset was not fetched"))
    if "/static/style.css" not in paths:
        report.findings.append(Finding("NO_CSS_FETCH", "low", "CSS asset was not fetched"))
    if "/favicon.ico" not in paths and events:
        report.findings.append(Finding("NO_FAVICON", "low", "favicon was not requested"))
    if "/static/lab.woff2" not in paths and events:
        report.findings.append(
            Finding("NO_FONT_RESOURCE", "low", "font resource was not requested")
        )
    if "/api/data" not in paths:
        report.findings.append(
            Finding("NO_FETCH_WATERFALL", "medium", "in-page fetch waterfall missing")
        )
    if "/api/echo-headers" not in paths and events:
        report.findings.append(Finding("NO_ECHO_FETCH", "low", "header echo fetch missing"))
    if "/redirect" not in paths and events:
        report.findings.append(Finding("NO_REDIRECT_REQUEST", "low", "redirect request missing"))
    redirect_final = any(
        path == "/api/data" and "redirect-final" in ((req.get("query") or {}).get("phase") or [])
        for path, req in zip(paths, requests, strict=False)
    )
    if "/redirect" in paths and not redirect_final:
        report.findings.append(Finding("NO_REDIRECT_FOLLOW", "medium", "redirect was not followed"))
    if not any(path == "/pixel" for path in paths):
        report.findings.append(Finding("NO_PIXEL", "low", "tracking pixel request missing"))
    if len(set(paths) & NETWORK_PATHS) < 4:
        report.findings.append(
            Finding(
                "THIN_WATERFALL", "medium", f"only {len(set(paths) & NETWORK_PATHS)} expected paths"
            )
        )

    ua = first_headers.get("user-agent", "")
    if not ua:
        report.findings.append(Finding("NO_UA_HEADER", "high", "document request lacks User-Agent"))
    if "python-httpx" in ua.lower() or "curl" in ua.lower():
        report.findings.append(Finding("RAW_CLIENT_UA", "high", f"non-browser UA: {ua!r}"))
    if "accept-language" not in first_headers:
        report.findings.append(
            Finding("NO_ACCEPT_LANGUAGE", "medium", "document lacks Accept-Language")
        )
    accept = first_headers.get("accept", "")
    if accept.strip() == "*/*":
        report.findings.append(Finding("HTML_ACCEPT_WILDCARD", "medium", "document Accept is */*"))
    if "accept-encoding" not in first_headers:
        report.findings.append(
            Finding("NO_ACCEPT_ENCODING", "low", "document lacks Accept-Encoding")
        )
    if "sec-fetch-site" not in first_headers and events:
        report.findings.append(
            Finding(
                "NO_FETCH_METADATA", "low", "browser-like JS ran but Sec-Fetch metadata is absent"
            )
        )
    elif events:
        expected_fetch = {
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
            "sec-fetch-site": "none",
        }
        for header, expected in expected_fetch.items():
            actual = first_headers.get(header)
            if actual and actual != expected:
                report.findings.append(
                    Finding(
                        "NAV_FETCH_METADATA_ODD",
                        "low",
                        f"{header}={actual!r}; expected document navigation shape {expected!r}",
                    )
                )
    if "chrome/" in ua.lower() and "sec-ch-ua" not in first_headers and events:
        report.findings.append(
            Finding("NO_UA_CH_HEADER", "low", "Chrome-like browser omitted sec-ch-ua on document")
        )
    client_ports = {int(req.get("client_port") or 0) for req in requests}
    if len(client_ports) > max(4, len(requests) // 2) and events:
        report.findings.append(
            Finding(
                "LOW_CONNECTION_REUSE",
                "low",
                f"{len(client_ports)} client ports for {len(requests)} requests",
            )
        )
    header_order = requests[0].get("header_order") or []
    if header_order and header_order[0] != "host":
        report.findings.append(
            Finding("ODD_HEADER_ORDER", "low", f"first document header is {header_order[0]!r}")
        )

    if probe:
        window = probe.get("window") or {}
        iframe = probe.get("iframe") or {}
        worker = probe.get("worker") or {}
        module_worker = probe.get("moduleWorker") or {}
        if window.get("webdriver") is True:
            report.findings.append(Finding("WEBDRIVER_TRUE", "high", "navigator.webdriver is true"))
        if iframe.get("webdriver") is True:
            report.findings.append(
                Finding("IFRAME_WEBDRIVER_TRUE", "high", "iframe webdriver is true")
            )
        if not worker or worker.get("error"):
            report.findings.append(
                Finding("NO_CLASSIC_WORKER", "medium", f"classic worker failed: {worker!r}")
            )
        if not module_worker or module_worker.get("error"):
            report.findings.append(
                Finding("NO_MODULE_WORKER", "medium", f"module worker failed: {module_worker!r}")
            )
        for realm_name, realm in (("worker", worker), ("module_worker", module_worker)):
            if not isinstance(realm, dict) or realm.get("error"):
                continue
            for axis in WORKER_AXES:
                if window.get(axis) != realm.get(axis):
                    report.findings.append(
                        Finding(
                            "REALM_MISMATCH",
                            "high",
                            f"{realm_name}.{axis}: window={window.get(axis)!r} realm={realm.get(axis)!r}",
                        )
                    )
        if isinstance(iframe, dict) and not iframe.get("error"):
            for axis in IFRAME_AXES:
                if window.get(axis) != iframe.get(axis):
                    report.findings.append(
                        Finding(
                            "IFRAME_REALM_MISMATCH",
                            "high",
                            f"iframe.{axis}: window={window.get(axis)!r} iframe={iframe.get(axis)!r}",
                        )
                    )
        elif events:
            report.findings.append(
                Finding("NO_IFRAME_REALM", "medium", f"iframe probe failed: {iframe!r}")
            )
        header_ua = first_headers.get("user-agent", "")
        if header_ua and window.get("userAgent") and header_ua != window.get("userAgent"):
            report.findings.append(
                Finding(
                    "HEADER_JS_UA_MISMATCH",
                    "high",
                    f"header UA {header_ua!r} != navigator.userAgent {window.get('userAgent')!r}",
                )
            )
        lang = str(window.get("language") or "")
        accept_language = first_headers.get("accept-language", "")
        if lang and accept_language and not accept_language.lower().startswith(lang.lower()):
            report.findings.append(
                Finding(
                    "HEADER_JS_LANGUAGE_MISMATCH",
                    "medium",
                    f"Accept-Language {accept_language!r} does not start with navigator.language {lang!r}",
                )
            )
        if not window.get("webglRenderer"):
            report.findings.append(Finding("NO_WEBGL", "medium", "window WebGL renderer is empty"))
        plugins = window.get("plugins") or []
        mime_types = window.get("mimeTypes") or []
        if "chrome/" in header_ua.lower() and not plugins:
            report.findings.append(
                Finding("ZERO_PLUGINS", "low", "Chrome-like navigator.plugins is empty")
            )
        if "chrome/" in header_ua.lower() and not mime_types:
            report.findings.append(
                Finding("ZERO_MIMETYPES", "low", "Chrome-like navigator.mimeTypes is empty")
            )
        ua_data = window.get("userAgentData")
        if "chrome/" in header_ua.lower() and not ua_data:
            report.findings.append(
                Finding("NO_JS_UA_DATA", "low", "Chrome-like navigator.userAgentData missing")
            )
        if isinstance(ua_data, dict):
            low_platform = str(ua_data.get("platform") or "")
            platform = str(window.get("platform") or "")
            if (
                low_platform
                and platform
                and low_platform.lower() not in platform.lower()
                and not (low_platform == "Windows" and platform.startswith("Win"))
            ):
                report.findings.append(
                    Finding(
                        "UA_DATA_PLATFORM_MISMATCH",
                        "medium",
                        f"userAgentData.platform={low_platform!r}, navigator.platform={platform!r}",
                    )
                )
        native = window.get("nativeIntegrity") or {}
        if native.get("automationGlobals"):
            report.findings.append(
                Finding(
                    "AUTOMATION_GLOBALS", "high", f"globals={native.get('automationGlobals')!r}"
                )
            )
        stack_sample = str(native.get("errorStackSample") or "").lower()
        if any(token in stack_sample for token in AUTOMATION_STACK_TOKENS):
            report.findings.append(
                Finding("AUTOMATION_STACK_TOKEN", "high", "error stack contains automation token")
            )
        if native and not native.get("functionToStringNative"):
            report.findings.append(
                Finding(
                    "FUNCTION_TOSTRING_PATCHED",
                    "medium",
                    "Function.prototype.toString is not native-shaped",
                )
            )
        if native and not native.get("fetchNative"):
            report.findings.append(
                Finding("FETCH_PATCHED", "medium", "window.fetch is not native-shaped")
            )
        chrome_shape = window.get("chromeShape") or {}
        if "chrome/" in header_ua.lower() and not chrome_shape.get("hasChrome"):
            report.findings.append(
                Finding("NO_WINDOW_CHROME", "medium", "Chrome UA lacks window.chrome")
            )
        storage = window.get("storage") or {}
        for key in ("localStorage", "sessionStorage", "indexedDB"):
            if storage.get(key) is False:
                report.findings.append(
                    Finding("STORAGE_UNAVAILABLE", "medium", f"{key} unavailable")
                )
        if storage and not storage.get("estimate"):
            report.findings.append(
                Finding("NO_STORAGE_ESTIMATE", "low", "navigator.storage.estimate missing")
            )
        canvas = window.get("canvas") or {}
        audio = window.get("audio") or {}
        if not canvas.get("hash"):
            report.findings.append(
                Finding("NO_CANVAS_FP", "medium", f"canvas unavailable: {canvas!r}")
            )
        if not audio.get("supported"):
            report.findings.append(
                Finding("NO_AUDIO_FP", "low", f"audio context unavailable: {audio!r}")
            )
        webgl = window.get("webgl") or {}
        if webgl.get("extensions") == []:
            report.findings.append(Finding("NO_WEBGL_EXTENSIONS", "low", "WebGL extensions empty"))
        media = window.get("mediaDevices") or {}
        if not media.get("supported"):
            report.findings.append(
                Finding("NO_MEDIA_DEVICES", "low", "navigator.mediaDevices unavailable")
            )
        performance = probe.get("performance") or {}
        if int(performance.get("resourceCount") or 0) < 5:
            report.findings.append(
                Finding(
                    "LOW_RESOURCE_TIMING",
                    "low",
                    f"resourceCount={performance.get('resourceCount')!r}",
                )
            )
        interaction = probe.get("interaction") or {}
        if events and not interaction.get("click"):
            report.findings.append(Finding("NO_CLICK_EVENT", "low", "no click event captured"))
        if events and not (interaction.get("mousemove") or interaction.get("pointermove")):
            report.findings.append(
                Finding("NO_POINTER_TRAIL", "low", "no pointer/mouse movement captured")
            )
        if events and not interaction.get("scroll"):
            report.findings.append(Finding("NO_SCROLL_EVENT", "low", "no scroll event captured"))
        user_activation = window.get("userActivation") or {}
        if (
            interaction.get("click")
            and user_activation
            and not user_activation.get("hasBeenActive")
        ):
            report.findings.append(
                Finding(
                    "USER_ACTIVATION_MISMATCH", "medium", "click captured but hasBeenActive=false"
                )
            )
    _add_deep_catalog_findings(
        report,
        requests=requests,
        events=events,
        paths=paths,
        first_headers=first_headers,
        probe=probe,
    )
    return report


def to_markdown(reports: list[ScoreReport]) -> str:
    lines = [
        "# Bot Parity Lab Report",
        "",
        "| client | verdict | score | signals | requests | events |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for report in reports:
        verdict = "PASS" if report.ok else "FAIL"
        lines.append(
            f"| `{report.client}` | {verdict} | {report.score} | {report.signal_count} | {report.request_count} | {report.event_count} |"
        )
    lines.append("")
    for report in reports:
        lines.append(f"## {report.client}")
        if not report.findings:
            lines.append("")
            lines.append("No medium/high bot-parity findings.")
            lines.append("")
            continue
        for finding in report.findings:
            lines.append(f"- **{finding.severity.upper()} {finding.code}**: {finding.detail}")
        lines.append("")
    return "\n".join(lines)
