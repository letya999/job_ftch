---
title: "079 — Proxy provider pool primitives"
description: "ADR for provider-neutral proxy routing primitives used by adaptive bypass."
updated: 2026-08-02
---
# 079 — Proxy provider pool primitives

**Status**: ACCEPTED
**Date**: 2026-08-02

## Context

Career-site ingest needs proxies only as an adaptive rescue route for protected
sites. A provider-specific string formatter inside `ResidentialProxyBypass` made
it too easy to add one-off branches and too hard to reason about multiple
providers, provider policies, sticky sessions, budgets, Playwright projection,
and secret-safe logging.

Current library docs confirm the lower-level integration points are already
covered by the existing stack:

- `httpx.AsyncClient(proxy=...)` / `AsyncHTTPTransport(proxy=...)` are the
  native async HTTP proxy integration points.
- Playwright supports proxy configuration at browser launch or context level
  with separate `server`, `username`, `password`, and bypass fields.

External proxy-rotation libraries researched for this change mostly solve public
proxy discovery/checking or crawler-framework coupling. ProxyBroker/ProxyBroker2
focus on finding/checking public proxies; Crawlee has a coherent proxy model but
would introduce a crawler runtime that conflicts with the current library-first
async source architecture. DataImpulse, BrightData, Oxylabs, Smartproxy, and
similar managed providers differ mostly in gateway username grammar, session
tokens, geo tokens, pricing, and blocked-target policy. Those differences should
be provider adapters, not source or pipeline logic.

## Decision

Introduce provider-neutral proxy primitives in
`job_ftch.infrastructure.bypass.proxy_pool`:

- `ProxyRouteRequest`: domain/country/session/purpose input to proxy selection.
- `ProxyEndpoint`: selected endpoint plus provider metadata, redacted logging,
  and Playwright proxy projection.
- `ProxyProviderSpec`: resolved provider config. Secrets are already provided by
  composition/settings/auth and are not loaded by the primitive itself.
- `GatewayProxyEndpointFactory`: provider adapter for managed gateway providers
  such as DataImpulse, BrightData, Oxylabs and Smartproxy.
- `ManagedProxyPool`: weighted pool across static endpoints and one or more
  provider factories, with sticky domain pins and allow/deny domain policy.

`ResidentialProxyBypass` remains the adapter to the existing bypass protocol and
route graph. It is responsible for:

- reading runtime settings;
- enforcing process-local byte budgets through `ProxyCostTracker`;
- applying selected endpoints to httpx/Playwright;
- reporting success/failure into bypass route state and domain intelligence.

The primitives deliberately do not import stores, source specs, monitors,
pipeline nodes, or Playwright/httpx clients. Persistent history belongs in
existing store/domain-intelligence boundaries; secrets belong in env/file/future
vault auth providers; routing activation belongs in adaptive bypass.

No new dependency is added. The current stack already has httpx and Playwright,
and the missing part is provider-neutral policy/session orchestration rather
than socket-level proxy support.

## Consequences

Adding a managed provider now means adding one small formatter branch or template
inside `GatewayProxyEndpointFactory`, plus tests. Source code and pipeline graph
do not learn provider names.

Several providers can be combined by constructing multiple `ProxyProviderSpec`
instances and passing them to `ManagedProxyPool`; the current env path still
creates a single provider for backward compatibility.

Domain policy is deny-first. Banking/government targets can be blocked globally
even if a provider-specific allowlist is broad.

Playwright receives split credentials instead of embedding secrets inside the
`server` URL, and logs/stats use redacted proxy URLs.

Future work, if the proxy tier becomes materially valuable:

- move process-local `ProxyCostTracker` counters into `Store` for cross-process
  budgets;
- persist provider/domain health summaries in `DomainIntelligence`;
- load multiple provider specs from a non-secret YAML plus credential references
  resolved through `AuthProvider`;
- add provider-specific compliance metadata for targets that require unblock or
  KYC review.

