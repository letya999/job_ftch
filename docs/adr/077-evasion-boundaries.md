---
title: "077 — Evasion boundaries: protections we do not attempt to bypass"
description: "**Status:** ACCEPTED"
updated: 2026-07-24
---
# 077 — Evasion boundaries: protections we do not attempt to bypass

**Status:** ACCEPTED
**Date:** 2026-07-21
**Related:** ADR-074, ADR-075, ADR-076

## Context

The bypass layer addresses HTTP/TLS fingerprinting, browser fingerprinting, behavioral analysis, and CAPTCHA challenges. However, several protection classes exist that cannot be defeated by software-only stealth techniques. Repeatedly investigating these categories wastes engineering time.

This ADR draws a hard boundary: the protections listed below are out of scope for the bypass layer. Any future ticket referencing them should be closed with a pointer to this ADR.

## Boundaries

### 1. JA4T / TCP-stack fingerprinting (#4)
OS kernel TCP parameters like window size, TTL, and MSS are set by the operating system kernel, not the application layer. Spoofing these requires root access, raw sockets, or a custom userspace TCP stack, which is impractical and unsafe in standard containerized Python environments.

### 2. HTTP/3 (QUIC) fingerprint (#6)
Currently, `curl_cffi` does not support HTTP/3 impersonation. QUIC fingerprinting is nascent but growing. When the underlying libraries like `curl_cffi` add stable QUIC support, this can be revisited.

### 3. Keystroke biometrics (#30)
Typing rhythm analysis requires real user input patterns. Synthetic patterns are statistically detectable. This is an authentication-layer signal, not a crawl-layer one, and is thus out of scope.

### 4. OCR / visual content extraction (#35)
Sites that render job data as images or inside canvas elements require Optical Character Recognition (OCR). This is a parser problem, not a bypass problem. If needed, this should be added as a `SiteParser`, not a bypass technique.

### 5. Authentication walls (#37)
Sites requiring login credentials are out of scope. Credential management introduces legal and security liability. These are covered by the source exclusion policy.

### 6. Device attestation / Web Environment Integrity (#38)
Hardware-backed attestation (such as Android SafetyNet, iOS DeviceCheck, or the proposed Web Environment Integrity) requires genuine hardware tokens. These cannot be reliably spoofed from a container environment.

### 7. W3C Web Bot Auth (#40)
This is actually an OPPORTUNITY, not a wall. Sites adopting the emerging W3C Bots Common Group standard could provide legitimate API access to declared bot agents. We will track this as a future integration point, rather than a bypass target.

### 8. Legal enforcement (#42)
Cease-and-desist letters, Terms of Service enforcement, and IP-based legal threats are not technical problems. These must be handled by the source exclusion policy and appropriate legal review.

## Consequences

### Positive
- Prevents wasted engineering time on unsolvable problems
- Creates clear escalation path: if a site deploys these protections, exclude the source rather than engineer around it
- Highlights W3C Web Bot Auth as a positive future direction

### Negative
- Some sites using these protections will remain inaccessible
- The boundary must be revisited if technology changes (e.g., HTTP/3 impersonation becomes available)

### Neutral
- Does not affect current bypass effectiveness — none of these protections are commonly deployed on career sites today
