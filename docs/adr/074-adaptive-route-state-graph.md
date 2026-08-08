---
title: "074 — Adaptive route-state graph and single execution context"
description: "**Status:** ACCEPTED"
updated: 2026-08-05
---
# 074 — Adaptive route-state graph and single execution context

**Status:** ACCEPTED
**Date:** 2026-07-20
**Supersedes in part:** ADR-037 and ADR-048
**Extends:** ADR-050, ADR-072 and ADR-073

## Context

The implemented adaptive bypass path is described as a linear list of named
tiers. That list currently mixes independent decisions:

- HTTP transport and TLS impersonation;
- browser engine;
- direct or proxy network route;
- browser-session ownership;
- CAPTCHA/challenge handling;
- behaviour and fingerprint decoration.

The runtime also has two state holders. `AdaptiveBypassManager` owns the current
strategy while `BypassContext` independently owns a preflight tier, persona and
proxy. Production HTTP calls do not consistently pass through the context, and
metrics can therefore describe the preflight recommendation rather than the
route that actually executed.

A fixed sequence such as
`stealth_browser -> camoufox -> nodriver -> proxy -> cloak` is not a safe policy.
For an IP/ASN block it launches several browsers before changing the network
route. For a parser error it may change transport even though transport is not
the cause. For a browser challenge it may discard a useful session before a
solver has a chance to use it.

## Decision

### One execution context

Each career-site run has one infrastructure-layer execution context. It owns:

- the absolute source deadline and attempt budgets;
- failure classification;
- effective route state;
- persona and session generation;
- network/proxy selection;
- browser lifecycle;
- challenge action;
- execution metrics and safe domain intelligence.

Preflight and cached intelligence may recommend the initial route. They do not
retain a second authoritative tier after execution starts.

### Orthogonal route state

The effective route is the composition of:

```text
transport: httpx | curl_stealth
browser: none | stealth_browser | nodriver | camoufox | cloak
network: direct | proxy
session: fresh | persistent | handoff
challenge: none | wait | solver
```

This is an infrastructure concern and must not add infrastructure types or I/O
to `domain/`.

Proxy, behaviour simulation, session handoff and CAPTCHA solving are
capabilities/actions. They are not positions in the browser engine list.

### Fallback order

Only when no more specific signal is available, the conservative fallback is:

```text
httpx/direct
-> curl_stealth/direct
-> stealth_browser/direct
-> nodriver/direct
-> camoufox/direct
-> cloak/direct
```

Registered capability metadata and deployment policy may remove an unavailable
or forbidden engine. Nodriver remains subject to ADR-073.

### Signal-specific transitions

| Evidence | Transition |
|---|---|
| TLS/JA3 or HTTP impersonation mismatch | `httpx -> curl_stealth` |
| JS shell or rendering requirement | choose `stealth_browser` |
| direct-CDP-compatible checkbox challenge | choose Nodriver when allowed |
| Chromium-specific fingerprint rejection | choose Camoufox |
| repeated 429 or IP/ASN block | retain viable transport/browser and switch network route |
| challenge in an open browser | retain session and run wait/solver policy |
| ordinary 5xx | bounded retry on the same route |
| parser error or normal-content empty result | change parser/monitor, not bypass |
| DNS, connection or certificate error | terminal/retry transport policy, not protection |

Cloak is a terminal browser engine and requires enough remaining deadline for
launch, navigation, extraction and cleanup.

### Parity-calibrated browser ordering

The 2026-08-05 owned parity-lab campaign replaced the assumption that every
browser tier is universally stronger than the previous one. For a generic
fingerprint rejection, installed capabilities are tried monotonically by cost:

```text
patchright_browser -> nodriver -> camoufox -> cloak
```

Nodriver advertises `fingerprint_resistant` and `generic_challenge` because its
headed Chromium run completed without hard automation findings after transport,
input and probe-order coherence fixes. Camoufox additionally advertises
`engine_diversity`; explicit Chromium/Blink-specific rejection therefore moves
to the Firefox engine rather than spending another Chromium attempt. Cloak
remains terminal until equivalent repeated parity evidence justifies a cheaper
position.

These are capability actions, not backend-name dispatch in the controller.
TLS, IP/rate, session and parser failures retain their independent route axes.

### Registry metadata

Selection remains registry-driven. Bypass registrations expose capability
metadata such as cost, browser family, session ownership, proxy support,
challenge support and legal/configuration gates. Core source code must not grow
backend-name `if/elif` dispatch.

### Failure and terminal outcomes

The classifier distinguishes at least:

```text
ok, rate_limit, captcha, challenge, blocked_ip, blocked_fingerprint,
auth_required, server_error, timeout, dns_error, connect_error, tls_error,
parser_error, parse_empty, board_gone, deadline, unknown
```

Response body/headers are inspected before a status-only fallback so a 403/503
challenge is not flattened into generic blocked/server failure. The primary
terminal outcome remains separate from soft/hard deadline flags as required by
ADR-072.

### FlareSolverr

FlareSolverr is not part of the supported runtime and must not be restored as a
tier, fallback, service or dependency.

## Consequences

- (+) IP and rate-limit failures can change network route without launching
  unrelated browser engines.
- (+) CAPTCHA solving can reuse the browser session that encountered the
  challenge.
- (+) Metrics describe the executed composition, not a stale tier label.
- (+) BrowserSessionBypass engines remain replaceable and registry-driven.
- (+) Retry/deadline budgets can be enforced across every transition.
- (-) The adaptive manager and context must be consolidated, which changes a
  broad integration seam.
- (-) Tests must cover policy transitions rather than asserting one universal
  tier order.
- (-) Cached strategy data requires migration from one tier string to a route
  representation.

## Verification

- Characterization tests must first prove the current double-controller,
  browser-delegation, classification and fingerprinter defects.
- Policy tests must prove that 429, server error and parser error cannot launch
  an irrelevant browser chain.
- Integration tests must execute each installed browser-session backend through
  the adaptive wrapper.
- Session-continuity tests must cover listing, pagination and details.
- The complete gates are tracked in `docs/plans/INGEST_ESCALATION_MASTER_PLAN.md`.
