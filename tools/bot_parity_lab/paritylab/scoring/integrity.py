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

def _header_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

def _language_family(value: str) -> str:
    return value.split(",", maxsplit=1)[0].split(";", maxsplit=1)[0].strip().lower().split("-", maxsplit=1)[0]

def _session_integrity_findings(session: SessionState) -> list[Finding]:
    """Correlate independent browser and server observations without collecting secrets."""
    findings: list[Finding] = []
    navigation = next((request for request in session.requests if request.path == "/"), None)
    window = _realm_map(session).get("window", {})
    if navigation and window:
        network_ua = navigation.first_header("user-agent") or ""
        runtime_ua = str(_deep_get(window, "runtime.userAgent", ""))
        if network_ua and runtime_ua and network_ua != runtime_ua:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "SESSION_NETWORK_RUNTIME_UA_MISMATCH",
                    "Network and runtime User-Agent differ",
                    "The navigation User-Agent and navigator.userAgent disagree within one same-origin session.",
                    evidence={
                        "network_ua_sha256": _header_digest(network_ua),
                        "runtime_ua_sha256": _header_digest(runtime_ua),
                    },
                    realms=["window"],
                    request_ids=[navigation.request_id],
                )
            )

        network_language = navigation.first_header("accept-language") or ""
        runtime_language = str(_deep_get(window, "runtime.language", ""))
        if (
            network_language
            and runtime_language
            and _language_family(network_language) != _language_family(runtime_language)
        ):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "SESSION_NETWORK_RUNTIME_LANGUAGE_CONFLICT",
                    "Network and runtime language families differ",
                    "Accept-Language and navigator.language identify incompatible primary language families.",
                    evidence={
                        "network_language_family": _language_family(network_language),
                        "runtime_language_family": _language_family(runtime_language),
                    },
                    realms=["window"],
                    request_ids=[navigation.request_id],
                )
            )

    worker_bootstrap_paths = frozenset(
        {
            "/static/classic-worker.js",
            "/static/module-worker.js",
            "/static/shared-worker.js",
            "/static/worker-common.js",
        }
    )
    foreground_requests = [
        request for request in session.requests if request.path not in worker_bootstrap_paths
    ]
    user_agents = {
        request.first_header("user-agent")
        for request in foreground_requests
        if request.first_header("user-agent")
    }
    if len(user_agents) > 1:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "SESSION_REQUEST_UA_DRIFT",
                "User-Agent drifted across one session",
                "Same-origin requests carried more than one User-Agent value.",
                evidence={"user_agent_hashes": sorted(_header_digest(value) for value in user_agents)},
                request_ids=[
                    request.request_id
                    for request in foreground_requests
                    if request.first_header("user-agent")
                ],
            )
        )

    languages = {
        _language_family(value)
        for request in foreground_requests
        if (value := request.first_header("accept-language"))
    }
    if len(languages) > 1:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "SESSION_REQUEST_LANGUAGE_DRIFT",
                "Accept-Language family drifted across one session",
                "Same-origin requests used incompatible primary language families.",
                evidence={"language_families": sorted(languages)},
                request_ids=[
                    request.request_id
                    for request in foreground_requests
                    if request.first_header("accept-language")
                ],
            )
        )

    if navigation:
        navigation_ua = navigation.first_header("user-agent") or ""
        worker_uas = {
            request.first_header("user-agent")
            for request in session.requests
            if request.path in worker_bootstrap_paths and request.first_header("user-agent")
        }
        if navigation_ua and worker_uas and worker_uas != {navigation_ua}:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "SESSION_WORKER_BOOTSTRAP_UA_DRIFT",
                    "Worker bootstrap identity differs from page context",
                    "Worker script bootstrap requests use a browser-process identity that differs from the foreground page context. This is recorded separately because it can precede context-level identity projection.",
                    evidence={
                        "navigation_ua_sha256": _header_digest(navigation_ua),
                        "worker_ua_hashes": sorted(_header_digest(value) for value in worker_uas),
                    },
                    request_ids=[
                        request.request_id
                        for request in session.requests
                        if request.path in worker_bootstrap_paths
                    ],
                )
            )

    primary_errors = [error for probe in session.probes if probe.realm == "window" for error in probe.errors]
    if primary_errors:
        stages = sorted(
            {
                str(error.get("stage", error.get("name", "unknown")))[:80]
                for error in primary_errors
            }
        )
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "SESSION_PRIMARY_PROBE_ERRORS",
                "Primary runtime probe reported errors",
                "The primary window probe completed with one or more collection errors, leaving a coverage gap in runtime evidence.",
                evidence={"error_count": len(primary_errors), "stages": stages},
                realms=["window"],
            )
        )
    ordered_probes = sorted(
        (probe for probe in session.probes if not probe.realm.startswith("vendor:")),
        key=lambda probe: probe.observed_at,
    )
    if ordered_probes:
        sequences = [probe.sequence for probe in ordered_probes]
        if any(current <= previous for previous, current in pairwise(sequences)):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "SESSION_PROBE_SEQUENCE_INVALID",
                    "Probe sequence is not strictly increasing",
                    "Probe submissions contain a duplicate or regressing sequence number, so cross-realm evidence cannot be trusted as one ordered capture.",
                    evidence={"sequences": sequences},
                    realms=[probe.realm for probe in ordered_probes],
                )
            )
    return findings
