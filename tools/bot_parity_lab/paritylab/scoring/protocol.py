from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from paritylab.models import (
    Finding,
    SessionState,
    SignalClass,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring.common import _finding


def _protocol_and_reputation_findings(
    session: SessionState,
    reputation: OfflineIPReputation,
) -> list[Finding]:
    findings: list[Finding] = []
    versions = Counter(request.http_version for request in session.requests)
    if versions and set(versions) == {"1.1"}:
        findings.append(
            _finding(
                SignalClass.LOW,
                "HTTP_ONLY_1_1",
                "Session used only HTTP/1.1",
                "No HTTP/2 or HTTP/3 request was observed. This is a weak local signal and can depend on client settings.",
                evidence={"versions": dict(versions)},
            )
        )
    if any(version.startswith("3") for version in versions):
        findings.append(
            _finding(
                SignalClass.INFO,
                "HTTP3_OBSERVED",
                "HTTP/3 observed",
                "At least one request arrived over QUIC/HTTP/3.",
                evidence={"versions": dict(versions)},
            )
        )
    elif versions:
        findings.append(
            _finding(
                SignalClass.INFO,
                "HTTP_VERSION_DISTRIBUTION",
                "HTTP protocol distribution captured",
                "The report includes the server-observed HTTP version for every request.",
                evidence={"versions": dict(versions)},
            )
        )

    observations = session.protocol_observations
    by_protocol = Counter(item.protocol for item in observations)
    if observations:
        findings.append(
            _finding(
                SignalClass.INFO,
                "NET_PROTOCOL_OBSERVATORY_CAPTURED",
                "Transport observatory evidence captured",
                "Decrypted or fixture-derived protocol metadata was normalized and fingerprinted.",
                evidence={"observations": dict(by_protocol)},
            )
        )
    for protocol in ("dns", "http2", "http3", "quic", "tcpip", "tls"):
        selected = [item for item in observations if item.protocol == protocol]
        fingerprints = {item.fingerprint for item in selected}
        if len(fingerprints) > 1:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    f"NET_{protocol.upper()}_FINGERPRINT_DRIFT",
                    f"{protocol.upper()} transport fingerprint drift",
                    "Multiple transport fingerprints were attributed to one logical session.",
                    evidence={
                        "observation_count": len(selected),
                        "fingerprints": sorted(fingerprints),
                        "sources": sorted({item.source for item in selected}),
                    },
                )
            )
    for observation in (item for item in observations if item.protocol == "quic"):
        alpn = observation.evidence.get("alpn")
        if isinstance(alpn, str) and not alpn.startswith("h3"):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_QUIC_ALPN_CONFLICT",
                    "QUIC observation does not negotiate HTTP/3",
                    "The QUIC transport observation advertises an ALPN outside the HTTP/3 family.",
                    evidence={"alpn": alpn, "fingerprint": observation.fingerprint},
                )
            )
        parameters = observation.evidence.get("transport_parameters")
        if not isinstance(parameters, list) or not parameters:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "NET_QUIC_PARAMETERS_EMPTY",
                    "QUIC transport parameters are empty",
                    "A QUIC observation was captured without normalized transport parameters.",
                    evidence={"fingerprint": observation.fingerprint},
                )
            )

    for observation in (item for item in observations if item.protocol == "tls"):
        evidence = observation.evidence
        if evidence.get("early_data_accepted") is True and evidence.get("resumed") is not True:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "TLS_EARLY_DATA_RESUMPTION_CONFLICT",
                    "Accepted early data lacks resumed TLS state",
                    "TLS lifecycle metadata marks 0-RTT as accepted without a resumed session.",
                    evidence={"fingerprint": observation.fingerprint},
                )
            )
        findings.append(
            _finding(
                SignalClass.INFO,
                "TLS_LIFECYCLE_OBSERVATORY_CAPTURED",
                "Decrypted TLS lifecycle captured",
                "Negotiated version, ALPN, resumption, tickets, key updates and handshake timing were normalized.",
                evidence={"fingerprint": observation.fingerprint, **evidence},
            )
        )

    for observation in (item for item in observations if item.protocol == "http3"):
        stream_types = observation.evidence.get("stream_types")
        settings = observation.evidence.get("settings")
        if not isinstance(stream_types, list) or 0 not in stream_types:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_HTTP3_CONTROL_STREAM_MISSING",
                    "HTTP/3 control stream missing",
                    "Decrypted HTTP/3 evidence does not contain a control stream.",
                    evidence={"fingerprint": observation.fingerprint},
                )
            )
        if not isinstance(settings, list) or not settings:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "NET_HTTP3_SETTINGS_EMPTY",
                    "HTTP/3 SETTINGS frame empty or missing",
                    "The control-stream evidence contains no normalized HTTP/3 settings.",
                    evidence={"fingerprint": observation.fingerprint},
                )
            )

    for observation in (item for item in observations if item.protocol == "dns"):
        evidence = observation.evidence
        transport = evidence.get("transport")
        if transport in {"dot", "doh", "doq"} and evidence.get("encrypted") is False:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_DNS_ENCRYPTION_CONFLICT",
                    "DNS transport encryption metadata conflicts",
                    "The resolver transport is encrypted by definition but the observation marks it unencrypted.",
                    evidence={"transport": str(transport)},
                )
            )
        query_count = evidence.get("query_count")
        qtypes = evidence.get("qtypes")
        if isinstance(query_count, int) and isinstance(qtypes, list):
            typed_count = sum(
                item[1]
                for item in qtypes
                if isinstance(item, list) and len(item) == 2 and isinstance(item[1], int)
            )
            if typed_count > query_count:
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "NET_DNS_QUERY_COUNT_CONFLICT",
                        "DNS query aggregates are inconsistent",
                        "The sum of QTYPE counts exceeds the reported query total.",
                        evidence={"query_count": query_count, "qtype_total": typed_count},
                    )
                )

    for observation in (item for item in observations if item.protocol == "tcpip"):
        evidence = observation.evidence
        option_order = evidence.get("option_order")
        if isinstance(option_order, list) and len(option_order) != len(set(option_order)):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "NET_TCP_OPTION_DUPLICATE",
                    "TCP SYN option order contains duplicates",
                    "The normalized SYN shape repeats an option that should occur once.",
                    evidence={"option_order": [str(item) for item in option_order]},
                )
            )
        hops = evidence.get("hop_estimate")
        if isinstance(hops, int) and hops > 64:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "NET_IP_HOP_ESTIMATE_HIGH",
                    "IP hop estimate is unusually high",
                    "The difference between initial and observed TTL exceeds 64 hops.",
                    evidence={"hop_estimate": hops},
                )
            )

    client_ips = sorted({request.client_host for request in session.requests})
    reputation_provenance = reputation.provenance()
    for ip in client_ips:
        match = reputation.lookup(ip)
        findings.append(
            _finding(
                SignalClass.INFO,
                "IP_REPUTATION_OFFLINE",
                "Offline IP policy match",
                "No public reputation service is queried. The result comes from the supplied CIDR policy and, when configured, a local MaxMind ASN database.",
                evidence={
                    "ip": match.ip,
                    "cidr": match.cidr,
                    "label": match.label,
                    "risk": match.risk,
                    "source": match.source,
                    "asn": match.asn,
                    "organization": match.organization,
                    "network_type": match.network_type,
                    "country": match.country,
                    "tags": list(match.tags),
                    "dataset": reputation_provenance,
                },
            )
        )
    return findings
