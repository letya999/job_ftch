# Boundaries and limitations

## What is real

- The TCP mirror reads the original cleartext TLS ClientHello before forwarding the exact bytes to the local TLS endpoint. JA3 and JA4 are computed from that observed ClientHello, not from client-declared metadata.
- Hypercorn records the protocol seen by the ASGI application: HTTP/1.1, HTTP/2 and, when `aioquic` is installed, HTTP/3.
- Header order is taken from ASGI `raw_headers`; request timing, connection reuse and response behavior are server-observed.
- Runtime, worker, iframe and behavior values are measured by code executing in the tested browser.

## What cannot be reproduced entirely on localhost

- Public ASN, IP reputation, residential/mobile classification and proxy history do not exist for a loopback address. The lab therefore uses an explicit offline CIDR policy and can enrich addresses from a local MaxMind ASN MMDB through `PARITYLAB_ASN_MMDB`. It never calls a reputation API.
- QUIC has no TLS-over-TCP ClientHello. The current report records HTTP/3 use but does not claim a JA4-Q/QUIC fingerprint. Adding passive QUIC Initial decryption is a separate extension.
- JA3/JA4 identify a TLS implementation/profile, not a human. They must be compared with manual baselines from the same OS/browser family.
- `isTrusted=true` does not prove a human. Native automation protocols can generate trusted input. Behavioral findings are weak unless combined with transport/runtime mismatches.
- Privacy settings, enterprise policies, disabled graphics/audio and minimal Linux images can legitimately remove APIs or entropy. Calibrate severity against accepted baselines.

## Proprietary anti-bot payloads

The `/api/opaque` endpoint records only content type, size, SHA-256, Shannon entropy, printable ratio, likely JSON/Base64 shape and JSON key names. It deliberately does not decrypt, reverse engineer, emulate or generate Akamai, Kasada, DataDome or other closed vendor tokens.

## Defensive scope

The server is hard-restricted to `localhost`, `127.0.0.0/8` or `::1`. The supplied clients only receive lab-generated loopback URLs. There is no proxy rotation, CAPTCHA solving, challenge bypass, third-party targeting or vendor-specific evasion logic.

## Protected playground

The opt-in protected playground models a career site, scrape-intent classifier,
proof-of-work, puzzle, clearance cookie, trap paths, and edge decisions. These
are owned local fixtures. They are useful for regression pressure on project
clients, but they are not equivalent to a production CDN account, external IP
reputation, managed bot score, or vendor-private challenge payload.
