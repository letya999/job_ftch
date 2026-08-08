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

def _notification_permission_api_state(value: object) -> object:
    if value == "default":
        return "prompt"
    return value

def _cross_realm_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    realms = _realm_map(session)
    window = realms.get("window")
    if not window:
        return findings

    expected_realms = {
        "iframe": SignalClass.MEDIUM,
        "classic-worker": SignalClass.MEDIUM,
        "module-worker": SignalClass.LOW,
    }
    for realm, signal_class in expected_realms.items():
        if realm not in realms:
            findings.append(
                _finding(
                    signal_class,
                    f"REALM_{realm.replace('-', '_').upper()}_MISSING",
                    f"Cross-realm probe missing: {realm}",
                    "The browser did not return the expected same-origin realm probe.",
                    realms=[realm],
                )
            )

    comparisons = [
        ("runtime.userAgent", SignalClass.HARD_BOT),
        ("runtime.platform", SignalClass.MEDIUM),
        ("runtime.language", SignalClass.MEDIUM),
        ("locale.timezone", SignalClass.MEDIUM),
        ("runtime.hardwareConcurrency", SignalClass.LOW),
    ]
    comparable_realms = ("iframe", "classic-worker", "module-worker", "shared-worker")
    for realm_name in comparable_realms:
        realm = realms.get(realm_name)
        if realm is None:
            continue
        for path, signal_class in comparisons:
            primary = _deep_get(window, path)
            secondary = _deep_get(realm, path)
            if primary is not None and secondary is not None and primary != secondary:
                findings.append(
                    _finding(
                        signal_class,
                        f"REALM_PARITY_{realm_name.replace('-', '_').upper()}_{path.replace('.', '_').upper()}",
                        f"Cross-realm mismatch in {path}",
                        f"The window and {realm_name} realms expose different values for {path}.",
                        evidence={"window": primary, realm_name: secondary},
                        realms=["window", realm_name],
                    )
                )

    iframe = realms.get("iframe")
    if iframe:
        window_renderer = _deep_get(window, "webgl.unmaskedRenderer")
        iframe_renderer = _deep_get(iframe, "webgl.unmaskedRenderer")
        if window_renderer and iframe_renderer and window_renderer != iframe_renderer:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "REALM_IFRAME_WEBGL_MISMATCH",
                    "WebGL renderer differs in iframe",
                    "Same-origin window and iframe realms expose different unmasked renderers.",
                    evidence={"window": window_renderer, "iframe": iframe_renderer},
                    realms=["window", "iframe"],
                )
            )

    for worker_name in ("classic-worker", "module-worker", "shared-worker"):
        worker = realms.get(worker_name)
        if not worker:
            continue
        offscreen = _deep_get(worker, "offscreen")
        if isinstance(offscreen, Mapping) and offscreen.get("supported") is True:
            worker_renderer = offscreen.get("unmaskedRenderer")
            window_renderer = _deep_get(window, "webgl.unmaskedRenderer")
            if worker_renderer and window_renderer and worker_renderer != window_renderer:
                findings.append(
                    _finding(
                        SignalClass.LOW,
                        f"REALM_{worker_name.replace('-', '_').upper()}_WEBGL_MISMATCH",
                        f"Offscreen WebGL differs in {worker_name}",
                        "Worker OffscreenCanvas and window WebGL renderers are inconsistent.",
                        evidence={"window": window_renderer, worker_name: worker_renderer},
                        realms=["window", worker_name],
                    )
                )
    return findings
