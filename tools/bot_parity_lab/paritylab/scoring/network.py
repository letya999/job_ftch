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
    TLSFingerprint,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring.tls import _tls_findings
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


def _network_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    requests = session.requests
    if not requests:
        return [
            _finding(
                SignalClass.HARD_BOT,
                "NET_NO_REQUESTS",
                "No request trace",
                "The session produced no server-observed requests, so browser parity cannot be established.",
            )
        ]

    navigation = next(
        (
            request
            for request in requests
            if request.path == "/" and request.first_header("sec-fetch-dest") == "document"
        ),
        next((request for request in requests if request.path == "/"), requests[0]),
    )
    headers = _header_map(navigation)
    ua = (headers.get("user-agent") or [""])[0]
    accept = (headers.get("accept") or [""])[0]
    language = (headers.get("accept-language") or [""])[0]
    encoding = (headers.get("accept-encoding") or [""])[0]

    if not ua:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "NET_UA_MISSING",
                "User-Agent is missing",
                "A top-level browser navigation normally carries a User-Agent header.",
                request_ids=[navigation.request_id],
            )
        )
    elif any(
        token in ua.lower() for token in ("python-httpx", "curl/", "python-requests", "scrapy")
    ):
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "NET_UA_NON_BROWSER",
                "Non-browser User-Agent",
                "The declared client is a raw HTTP library rather than a browser engine.",
                evidence={"user_agent": ua},
                request_ids=[navigation.request_id],
            )
        )
    if not accept or "text/html" not in accept:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_ACCEPT_NAV_MISMATCH",
                "Navigation Accept header is atypical",
                "Top-level browser navigation usually advertises HTML and related document formats.",
                evidence={"accept": accept},
                request_ids=[navigation.request_id],
            )
        )
    if not language:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_ACCEPT_LANGUAGE_MISSING",
                "Accept-Language is missing",
                "A browser profile normally sends at least one preferred language.",
                request_ids=[navigation.request_id],
            )
        )
    if not encoding:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_ACCEPT_ENCODING_MISSING",
                "Accept-Encoding is missing",
                "A browser usually negotiates compressed content encodings.",
                request_ids=[navigation.request_id],
            )
        )

    sec_fetch = {
        name: (headers.get(name) or [None])[0]
        for name in ("sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user")
    }
    missing_sec_fetch = [name for name, value in sec_fetch.items() if value is None]
    if len(missing_sec_fetch) >= 3:
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "NET_SEC_FETCH_ABSENT",
                "Sec-Fetch metadata is absent",
                "The navigation lacks most Fetch Metadata headers expected from current browsers.",
                evidence={"missing": missing_sec_fetch},
                request_ids=[navigation.request_id],
            )
        )
    elif missing_sec_fetch:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_SEC_FETCH_PARTIAL",
                "Sec-Fetch metadata is incomplete",
                "The Fetch Metadata header set is only partially present.",
                evidence={"missing": missing_sec_fetch, "observed": sec_fetch},
                request_ids=[navigation.request_id],
            )
        )

    chromium_declared = any(token in ua for token in ("Chrome/", "Chromium/", "Edg/"))
    ch_ua = (headers.get("sec-ch-ua") or [""])[0]
    if chromium_declared and not ch_ua:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_CH_UA_MISSING",
                "Chromium UA lacks Client Hints",
                "The User-Agent declares Chromium while Sec-CH-UA is absent on the navigation.",
                evidence={"user_agent": ua},
                request_ids=[navigation.request_id],
            )
        )
    if ch_ua and not chromium_declared:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_CH_UA_FAMILY_CONFLICT",
                "Client Hints conflict with User-Agent",
                "Sec-CH-UA is present but the User-Agent does not declare a Chromium-family browser.",
                evidence={"user_agent": ua, "sec_ch_ua": ch_ua},
                request_ids=[navigation.request_id],
            )
        )

    expected_paths = {
        "/static/probe.js": SignalClass.HARD_BOT,
        "/static/style.css": SignalClass.MEDIUM,
        "/static/pixel.svg": SignalClass.MEDIUM,
        "/favicon.ico": SignalClass.LOW,
        "/api/fetch": SignalClass.HARD_BOT,
        "/api/beacon": SignalClass.MEDIUM,
        "/api/cookie/set": SignalClass.MEDIUM,
        "/api/cookie/echo": SignalClass.MEDIUM,
        "/api/redirect/start": SignalClass.MEDIUM,
        "/api/redirect/mid": SignalClass.LOW,
        "/api/redirect/final": SignalClass.MEDIUM,
        "/api/cacheable": SignalClass.MEDIUM,
        "/static/classic-worker.js": SignalClass.MEDIUM,
        "/static/module-worker.js": SignalClass.LOW,
    }
    observed_paths = {request.path for request in requests}
    for path, signal_class in expected_paths.items():
        if path not in observed_paths:
            findings.append(
                _finding(
                    signal_class,
                    f"NET_RESOURCE_MISSING_{path.replace('/', '_').strip('_').upper()}",
                    f"Expected resource was not requested: {path}",
                    "A normal page load executes a fixed local resource graph; the request is absent.",
                    evidence={"missing_path": path},
                )
            )

    connection_ids = [request.connection_id for request in requests if request.connection_id]
    unique_connections = len(set(connection_ids))
    if len(connection_ids) >= 8:
        ratio = unique_connections / len(connection_ids)
        if ratio > 0.75:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_CONNECTION_REUSE_LOW",
                    "Low connection reuse",
                    "Most resources arrived on separate TLS connections rather than a reused browser connection pool.",
                    evidence={
                        "requests_with_connection_id": len(connection_ids),
                        "unique_connections": unique_connections,
                        "unique_ratio": round(ratio, 3),
                    },
                )
            )

    first_ns = min(request.monotonic_ns for request in requests)
    last_ns = max(request.monotonic_ns for request in requests)
    span_ms = (last_ns - first_ns) / 1_000_000
    if len(requests) >= 8 and span_ms < 25:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "NET_WATERFALL_COLLAPSED",
                "Request waterfall is implausibly compressed",
                "The full page resource graph completed in a very narrow arrival window.",
                evidence={"request_count": len(requests), "span_ms": round(span_ms, 3)},
            )
        )

    cookie_set = [request for request in requests if request.path == "/api/cookie/set"]
    cookie_echo = [request for request in requests if request.path == "/api/cookie/echo"]
    if cookie_set and cookie_echo:
        if min(request.monotonic_ns for request in cookie_echo) < min(
            request.monotonic_ns for request in cookie_set
        ):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_COOKIE_TRANSACTION_ORDER",
                    "Cookie echo preceded cookie set",
                    "The local cookie echo request arrived before the endpoint that establishes the cookie lifecycle.",
                    request_ids=[
                        *(request.request_id for request in cookie_set),
                        *(request.request_id for request in cookie_echo),
                    ],
                )
            )
        echoed_cookie = cookie_echo[-1].first_header("cookie") or ""
        if "lab_cookie=" not in echoed_cookie:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_COOKIE_NOT_RETURNED",
                    "Session cookie was not returned",
                    "The server set a secure first-party cookie, but the later echo request did not include it.",
                    evidence={"cookie_header": echoed_cookie},
                    request_ids=[cookie_echo[-1].request_id],
                )
            )

    cache_requests = [request for request in requests if request.path == "/api/cacheable"]
    if len(cache_requests) >= 2:
        conditional = any(
            request.first_header("if-none-match") or request.first_header("if-modified-since")
            for request in cache_requests[1:]
        )
        status_304 = any(request.response_status == 304 for request in cache_requests[1:])
        if not conditional and not status_304:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "NET_CACHE_REVALIDATION_ABSENT",
                    "No cache revalidation observed",
                    "Repeated local cache probes did not produce a conditional request or 304 response.",
                    request_ids=[request.request_id for request in cache_requests],
                )
            )

    header_names = [name.lower() for name, _ in navigation.headers]
    if header_names and header_names[0] in {"accept", "user-agent"}:
        findings.append(
            _finding(
                SignalClass.LOW,
                "NET_HEADER_ORDER_UNUSUAL",
                "Header order differs from common browser layouts",
                "The first normal header is atypical for a browser navigation. Header order is only a weak signal.",
                evidence={"header_order": header_names[:12]},
                request_ids=[navigation.request_id],
            )
        )

    redirect_paths = ("/api/redirect/start", "/api/redirect/mid", "/api/redirect/final")
    redirect_hops = {
        path: min(
            (request for request in requests if request.path == path),
            key=lambda request: request.monotonic_ns,
            default=None,
        )
        for path in redirect_paths
    }
    observed_redirects = [redirect_hops[path] for path in redirect_paths if redirect_hops[path]]
    if len(observed_redirects) == len(redirect_paths):
        hop_times = [request.monotonic_ns for request in observed_redirects]
        if hop_times != sorted(hop_times):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_REDIRECT_CHAIN_ORDER",
                    "Redirect chain arrived out of order",
                    "The fixed local redirect sequence did not arrive in start, mid, final order.",
                    evidence={"paths": list(redirect_paths)},
                    request_ids=[request.request_id for request in observed_redirects],
                )
            )

    fetch_requests = [request for request in requests if request.path == "/api/fetch"]
    if fetch_requests:
        fetch = fetch_requests[-1]
        if fetch.first_header("sec-fetch-dest") not in {"empty", None}:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "NET_FETCH_DEST_MISMATCH",
                    "Fetch request has atypical destination metadata",
                    "A JavaScript fetch normally uses Sec-Fetch-Dest: empty.",
                    evidence={"sec_fetch_dest": fetch.first_header("sec-fetch-dest")},
                    request_ids=[fetch.request_id],
                )
            )

    return findings
