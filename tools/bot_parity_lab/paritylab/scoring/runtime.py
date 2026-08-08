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
from paritylab.scoring.common import (
    CATALOG_SEVERITY_CLASS,
    HARD_WEIGHT,
    LOW_WEIGHT,
    MEDIUM_WEIGHT,
    _catalog_snapshot,
    _deep_get,
    _finding,
    _header_map,
    _light_interaction,
    _light_request,
    _light_window,
    _realm_map,
)
from paritylab.scoring.realm import _notification_permission_api_state

def _runtime_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    realms = _realm_map(session)
    window = realms.get("window")
    if window is None:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "JS_WINDOW_PROBE_MISSING",
                "Window JavaScript probe missing",
                "The page did not execute or submit the primary browser runtime probe.",
            )
        )
        return findings

    webdriver = _deep_get(window, "runtime.webdriver")
    if webdriver is True:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "JS_NAVIGATOR_WEBDRIVER",
                "navigator.webdriver is true",
                "The browser explicitly exposes WebDriver automation state.",
                evidence={"navigator.webdriver": True},
                realms=["window"],
            )
        )

    ua = str(_deep_get(window, "runtime.userAgent", ""))
    if "HeadlessChrome" in ua or "Headless" in ua:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "JS_HEADLESS_UA",
                "Headless token in User-Agent",
                "The JavaScript User-Agent directly identifies a headless runtime.",
                evidence={"user_agent": ua},
                realms=["window"],
            )
        )

    languages = _deep_get(window, "runtime.languages", [])
    language = _deep_get(window, "runtime.language", "")
    if not isinstance(languages, list) or not languages:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_LANGUAGES_EMPTY",
                "navigator.languages is empty",
                "A normal interactive browser profile normally exposes at least one preferred language.",
                realms=["window"],
            )
        )
    elif language and languages[0] != language:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_LANGUAGE_ORDER_MISMATCH",
                "Primary language differs from languages[0]",
                "navigator.language and the first navigator.languages value are inconsistent.",
                evidence={"language": language, "languages": languages},
                realms=["window"],
            )
        )

    platform = str(_deep_get(window, "runtime.platform", ""))
    ua_platform = str(_deep_get(window, "runtime.userAgentData.platform", ""))
    if platform and ua_platform:
        normalized_platform = platform.lower()
        normalized_ua_platform = ua_platform.lower()
        compatible = (
            ("win" in normalized_platform and "win" in normalized_ua_platform)
            or ("mac" in normalized_platform and "mac" in normalized_ua_platform)
            or ("linux" in normalized_platform and "linux" in normalized_ua_platform)
            or ("android" in normalized_ua_platform and "linux" in normalized_platform)
        )
        if not compatible:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "JS_PLATFORM_UACH_CONFLICT",
                    "Platform conflicts with UA Client Hints",
                    "navigator.platform and navigator.userAgentData.platform describe different OS families.",
                    evidence={"platform": platform, "ua_ch_platform": ua_platform},
                    realms=["window"],
                )
            )

    chrome_exists = _deep_get(window, "chrome.exists")
    if any(token in ua for token in ("Chrome/", "Chromium/", "Edg/")) and chrome_exists is False:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_CHROME_OBJECT_MISSING",
                "Chromium UA lacks window.chrome",
                "The declared Chromium-family browser does not expose the expected chrome object.",
                realms=["window"],
            )
        )

    inner_width = _deep_get(window, "window.innerWidth", 0)
    inner_height = _deep_get(window, "window.innerHeight", 0)
    outer_width = _deep_get(window, "window.outerWidth", 0)
    outer_height = _deep_get(window, "window.outerHeight", 0)
    if min(int(inner_width or 0), int(inner_height or 0)) <= 0:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "JS_ZERO_VIEWPORT",
                "Viewport dimensions are zero",
                "The document has no plausible interactive viewport.",
                evidence={"innerWidth": inner_width, "innerHeight": inner_height},
                realms=["window"],
            )
        )
    elif int(outer_width or 0) == 0 or int(outer_height or 0) == 0:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_OUTER_DIMENSIONS_ZERO",
                "Outer window dimensions are zero",
                "The viewport exists but outer browser-window dimensions are absent.",
                evidence={"outerWidth": outer_width, "outerHeight": outer_height},
                realms=["window"],
            )
        )

    webgl_available = _deep_get(window, "webgl.available")
    renderer = str(_deep_get(window, "webgl.unmaskedRenderer", ""))
    if webgl_available is False:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_WEBGL_UNAVAILABLE",
                "WebGL is unavailable",
                "A current desktop browser normally exposes at least WebGL 1 in this local secure context.",
                realms=["window"],
            )
        )
    elif any(token in renderer.lower() for token in ("swiftshader", "llvmpipe", "software rasterizer")):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_SOFTWARE_WEBGL",
                "Software WebGL renderer",
                "The unmasked WebGL renderer identifies a software rasterizer often seen in headless or containerized environments.",
                evidence={"renderer": renderer},
                realms=["window"],
            )
        )

    if not _deep_get(window, "canvas.hash"):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_CANVAS_PROBE_FAILED",
                "Canvas fingerprint unavailable",
                "Canvas rendering or readback did not produce a stable local digest.",
                realms=["window"],
            )
        )
    if not _deep_get(window, "audio.hash"):
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_AUDIO_PROBE_FAILED",
                "Audio fingerprint unavailable",
                "OfflineAudioContext rendering did not produce a digest. Privacy settings can also cause this.",
                realms=["window"],
            )
        )

    plugin_count = int(_deep_get(window, "plugins.pluginCount", 0) or 0)
    if any(token in ua for token in ("Chrome/", "Chromium/", "Edg/")) and plugin_count == 0:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_PLUGINS_EMPTY_CHROMIUM",
                "Chromium plugin array is empty",
                "Current Chromium profiles commonly expose built-in PDF viewer plugin entries.",
                realms=["window"],
            )
        )

    native_ok = _deep_get(window, "codeIntegrity.functionToStringNative")
    patched_samples = _deep_get(window, "codeIntegrity.nonNativeExpected", [])
    if native_ok is False:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "JS_FUNCTION_TOSTRING_PATCHED",
                "Function.prototype.toString is not native-shaped",
                "The core function source serializer appears replaced or wrapped.",
                realms=["window"],
            )
        )
    if isinstance(patched_samples, list) and patched_samples:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_NATIVE_FUNCTION_SHAPE_MISMATCH",
                "Expected native functions have non-native source shapes",
                "One or more built-in browser functions do not stringify as native code.",
                evidence={"functions": patched_samples},
                realms=["window"],
            )
        )

    suspicious_globals = _deep_get(window, "automation.suspiciousGlobals", [])
    if isinstance(suspicious_globals, list) and suspicious_globals:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "CDP_AUTOMATION_GLOBALS",
                "Automation globals detected",
                "Known Playwright/Puppeteer/Selenium global markers are present in the page realm.",
                evidence={"globals": suspicious_globals},
                realms=["window"],
            )
        )

    stack_markers = _deep_get(window, "automation.stackMarkers", [])
    if isinstance(stack_markers, list) and stack_markers:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "CDP_STACK_MARKERS",
                "Automation-related stack markers detected",
                "A locally generated Error stack contains evaluation or automation framework markers.",
                evidence={"markers": stack_markers},
                realms=["window"],
            )
        )

    notification_state = _deep_get(window, "permissions.states.notifications")
    notification_api = _deep_get(window, "notifications.permission")
    if (
        notification_state
        and notification_api
        and notification_state != _notification_permission_api_state(notification_api)
    ):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "JS_PERMISSION_NOTIFICATION_CONFLICT",
                "Notification permission APIs disagree",
                "Permissions.query and Notification.permission returned different states.",
                evidence={
                    "permissions_query": notification_state,
                    "notification_permission": notification_api,
                },
                realms=["window"],
            )
        )

    speech_count = int(_deep_get(window, "speech.count", 0) or 0)
    if speech_count == 0:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_SPEECH_VOICES_EMPTY",
                "No speech synthesis voices",
                "The browser exposed no speechSynthesis voices. This is common in minimal containers but is not conclusive.",
                realms=["window"],
            )
        )

    return findings
