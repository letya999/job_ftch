from __future__ import annotations

import json
from collections import Counter
from typing import Any

from paritylab.models import Finding, ScoreSummary, SessionState, SignalClass, json_safe


def _escape(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text[:400]


def _json_inline(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True)[:1000]


def render_markdown(
    session: SessionState,
    findings: list[Finding],
    summary: ScoreSummary,
) -> str:
    lines: list[str] = []
    lines.extend(
        [
            "# Bot / Browser Parity Report",
            "",
            f"- **Session:** `{session.session_id}`",
            f"- **Client:** `{session.client_name}` (`{session.client_family}`)",
            f"- **Created:** `{session.created_at}`",
            f"- **Disposition:** **{summary.disposition.value}**",
            f"- **Suspicion score:** **{summary.score}/100**",
            f"- **Counts:** hard={summary.hard_count}, medium={summary.medium_count}, low={summary.low_count}, info={summary.info_count}",
            f"- **Gate reason:** {summary.gate_reason}",
            "",
            "> This is a local defensive audit. The score is heuristic and should be calibrated against your own manual-browser baselines.",
            "",
            "## Findings",
            "",
            "| Class | Score | Code | Finding | Exact reason |",
            "|---|---:|---|---|---|",
        ]
    )
    if findings:
        for finding in findings:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape(finding.signal_class.value),
                        str(finding.severity_score),
                        f"`{_escape(finding.code)}`",
                        _escape(finding.title),
                        _escape(finding.reason),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| informational | 0 | `NONE` | No findings | No rules fired. |")

    lines.extend(["", "## Evidence", ""])
    for finding in findings:
        if finding.evidence or finding.realms or finding.request_ids:
            lines.append(f"### `{finding.code}`")
            if finding.realms:
                lines.append(f"- Realms: `{', '.join(finding.realms)}`")
            if finding.request_ids:
                lines.append(f"- Request IDs: `{', '.join(finding.request_ids)}`")
            if finding.evidence:
                lines.append(f"- Evidence: `{_json_inline(finding.evidence)}`")
            lines.append("")

    tls = session.tls_fingerprints
    lines.extend(["## TLS / Transport", ""])
    if tls:
        lines.extend(
            [
                "| Connection | Client | JA3 | JA4 | ALPN | TLS versions | Parse error |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for item in tls:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{item.connection_id[:12]}`",
                        f"`{item.client_host}:{item.client_port}`",
                        f"`{item.ja3 or ''}`",
                        f"`{item.ja4 or ''}`",
                        _escape(",".join(item.alpn_protocols)),
                        _escape(",".join(hex(value) for value in item.supported_versions)),
                        _escape(item.parse_error or ""),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No TLS ClientHello was correlated with this session.")

    lines.extend(["", "## Request waterfall", ""])
    lines.extend(
        [
            "| # | +ms | Duration | HTTP | Connection | Method | Path | Status | Fetch metadata |",
            "|---:|---:|---:|---|---|---|---|---:|---|",
        ]
    )
    ordered = sorted(session.requests, key=lambda item: item.monotonic_ns)
    base = ordered[0].monotonic_ns if ordered else 0
    for index, request in enumerate(ordered, start=1):
        sec_fetch = "/".join(
            filter(
                None,
                [
                    request.first_header("sec-fetch-site"),
                    request.first_header("sec-fetch-mode"),
                    request.first_header("sec-fetch-dest"),
                ],
            )
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"{(request.monotonic_ns - base) / 1_000_000:.2f}",
                    f"{request.duration_ms:.2f} ms",
                    _escape(request.http_version),
                    f"`{(request.connection_id or '-')[:12]}`",
                    request.method,
                    f"`{_escape(request.path)}`",
                    str(request.response_status),
                    _escape(sec_fetch),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Header order", ""])
    navigation = next((item for item in ordered if item.path == "/"), None)
    if navigation:
        for index, (name, value) in enumerate(navigation.headers, start=1):
            lines.append(f"{index}. `{name}: {_escape(value)}`")
    else:
        lines.append("No navigation request.")

    lines.extend(["", "## JavaScript realms", ""])
    if session.probes:
        lines.extend(
            [
                "| Realm | Sequence | Errors | UA | Platform | Language | Timezone | WebGL renderer |",
                "|---|---:|---:|---|---|---|---|---|",
            ]
        )
        for probe in sorted(session.probes, key=lambda item: item.sequence):
            runtime = probe.data.get("runtime", {})
            locale = probe.data.get("locale", {})
            webgl = probe.data.get("webgl", probe.data.get("offscreen", {}))
            if not isinstance(runtime, dict):
                runtime = {}
            if not isinstance(locale, dict):
                locale = {}
            if not isinstance(webgl, dict):
                webgl = {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape(probe.realm),
                        str(probe.sequence),
                        str(len(probe.errors)),
                        _escape(runtime.get("userAgent", "")),
                        _escape(runtime.get("platform", "")),
                        _escape(runtime.get("language", "")),
                        _escape(locale.get("timezone", "")),
                        _escape(webgl.get("unmaskedRenderer", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No JavaScript probes.")

    lines.extend(["", "## Behavioral summary", ""])
    event_counts = Counter(event.event_type for event in session.behavior)
    lines.append(f"- Event count: **{len(session.behavior)}**")
    lines.append(f"- Event types: `{_json_inline(dict(event_counts))}`")
    trusted = [event.trusted for event in session.behavior if event.trusted is not None]
    lines.append(f"- Trusted events: **{sum(value is True for value in trusted)} / {len(trusted)}**")
    if session.behavior:
        first = min(event.since_navigation_ms for event in session.behavior)
        last = max(event.since_navigation_ms for event in session.behavior)
        lines.append(f"- Observed behavior window: **{first:.1f}–{last:.1f} ms**")

    lines.extend(["", "## Opaque local payload observations", ""])
    if session.opaque_payloads:
        lines.extend(
            [
                "| Request | Bytes | Entropy | Printable | JSON-like | Base64-like | SHA-256 |",
                "|---|---:|---:|---:|---|---|---|",
            ]
        )
        for item in session.opaque_payloads:
            lines.append(
                f"| `{item.request_id[:12]}` | {item.body_bytes} | {item.shannon_entropy:.3f} | "
                f"{item.printable_ratio:.3f} | {item.likely_json} | {item.likely_base64} | `{item.sha256}` |"
            )
    else:
        lines.append("No opaque payload samples were submitted.")

    if session.intent is not None or session.gate_decisions or session.challenges or session.trap_hits:
        lines.extend(["", "## Playground (protected site, gate, challenges)", ""])
        if session.intent is not None:
            intent = session.intent
            lines.extend(
                [
                    f"- **Classified intent:** `{intent.intent}` (confidence {intent.confidence:.2f})",
                    f"- **Distinct jobs touched:** {intent.distinct_jobs} "
                    f"(coverage {intent.coverage_ratio:.1%} of the owned catalog)",
                    f"- **Listing pages:** {intent.listing_pages}, **API requests:** {intent.api_requests}",
                    f"- **Velocity:** {intent.velocity_rps} req/s, median gap {intent.median_gap_ms} ms",
                    f"- **Surfaces:** `{_json_inline(dict(intent.surfaces))}`",
                    f"- **Trap hits:** {intent.trap_hits}",
                ]
            )
        if session.gate_decisions:
            decision_counts = Counter(item.decision.value for item in session.gate_decisions)
            lines.append(f"- **Gate decisions:** `{_json_inline(dict(decision_counts))}`")
            lines.extend(
                [
                    "",
                    "| Time | Path | Decision | Reason | Detail |",
                    "|---|---|---|---|---|",
                ]
            )
            for item in session.gate_decisions[-30:]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _escape(item.observed_at),
                            f"`{_escape(item.request_path)}`",
                            item.decision.value,
                            f"`{_escape(item.reason_code)}`",
                            _escape(item.detail),
                        ]
                    )
                    + " |"
                )
        if session.challenges:
            lines.extend(
                [
                    "",
                    "| Challenge hash | Kind | Outcome | Attempts | Resolved |",
                    "|---|---|---|---:|---|",
                ]
            )
            for item in session.challenges[-30:]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            f"`{item.challenge_id_hash}`",
                            item.kind,
                            item.outcome.value,
                            str(item.attempts),
                            _escape(item.resolved_at or ""),
                        ]
                    )
                    + " |"
                )
        if session.trap_hits:
            lines.append(f"- **Honeypot paths requested:** `{_json_inline(list(session.trap_hits))}`")

    lines.append("")
    lines.append(
        "The lab records shape features only. It does not decode or emulate closed vendor payloads."
    )

    lines.extend(
        [
            "",
            "## Calibration notes",
            "",
            "- Build baselines from several headed manual sessions on each supported OS/browser version.",
            "- Treat single low-entropy mismatches as weak evidence; combine independent layers.",
            "- Re-run after browser upgrades because TLS, Client Hints, permissions, and API surfaces change.",
            "- Keep the gate focused on regressions relative to your own accepted baseline, not universal allow/deny claims.",
        ]
    )
    return "\n".join(lines)
