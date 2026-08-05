# Open-source component policy

External projects have explicit roles. Every runtime candidate needs an official-source check, license review, pinned version, isolated adapter and owned fixture.

| Component | Role | Status |
|---|---|---|
| Playwright | Browser harness and trace oracle | adopted |
| Patchright | Comparison client | adopted optional |
| Nodriver | Comparison client | adopted optional |
| Camoufox | Firefox-derived comparison client | adopted optional |
| [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs) 5.2.0 | Independent browser collector | adopted local ESM; SHA-256 pinned |
| [ThumbmarkJS](https://github.com/thumbmarkjs/thumbmarkjs) 1.10.1 | Rendering/font collector cross-check | adopted local ESM; telemetry disabled; SHA-256 pinned |
| [BotD](https://github.com/fingerprintjs/BotD) 2.0.0 | Independent automation detector | adopted local ESM; SHA-256 pinned |
| [Creep research corpus](https://github.com/abrahamjuliot/creepjs) | Prototype-lie and privacy-resistance oracle | review-only; rename and hosting/trademark review required |
| aioquic | QUIC/HTTP3 server | adopted through Hypercorn |
| dpkt | Offline packet parser | evaluation |
| Scapy | Packet experiment tool | external profile planned |
| [Wireshark/tshark](https://www.wireshark.org/docs/man-pages/tshark.html) | Frame-level protocol oracle | integrated privacy-safe `-T json` converter |
| mitmproxy | Controlled application-flow oracle | external integration planned |
| JA4 | TLS fingerprint standard | partial adopted; JA4+ license gate required |
| uTLS | Impersonation negative control | external fixture planned |
| curl-impersonate | TLS/HTTP2 negative control | external fixture planned |
| OpenWPM | Research measurement oracle | evaluation |

## Admission gate

1. Verify the official repository and maintenance status.
2. Record license and redistribution constraints.
3. Pin a reviewed version and lock transitive dependencies.
4. Put the component behind a namespaced adapter.
5. Add a deterministic owned fixture and failure-mode test.
6. Record covered and unavailable surfaces.
7. Keep canonical facts, finding codes and policy inside parity lab.

Third-party results use namespaces such as `fingerprintjs.*`, `thumbmark.*` and `botd.*`. They are evidence, not authoritative visitor identities.

## Executable admission

`data/oss_components.json` is the machine-readable source of truth. Run:

```bash
python scripts/audit_oss_registry.py
```

Browser assets are never loaded from a CDN. An adapter may accept namespaced fixture evidence while
its status is `adapter-ready`; execution is allowed only after the reviewed local bundle has an exact
SHA-256 and the registry status becomes `adopted`. The endpoint is
`POST /api/vendor/{component}?sid=...`; it stores the component id, pinned version, mode and result
under `vendor:{component}` without promoting a third-party visitor id or verdict to a canonical fact.

Convert an already-filtered TShark JSON export without retaining IP addresses, ports or packets:

```bash
python scripts/tshark_to_observatory.py capture.json
```

Post the result to `/api/observatory/tcpip`. Capture permissions and packet retention remain outside
the lab; only normalized TTL/window/MSS/options/SYN pacing evidence enters the artifact.
