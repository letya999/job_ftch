---
title: "100 — Node-scoped LLM providers and graceful degradation"
description: "Graph nodes select named provider/model bindings; provider outages degrade dependent work without stopping the service."
updated: 2026-09-18
---
# 100 — Node-scoped LLM providers and graceful degradation

**Status**: PROPOSED  
**Date**: 2026-09-18

## Context

The active runtime has one effective LLM provider. Selecting the CLIProxyAPI
gateway also overlays extraction, relevance and ontology models. This couples
transport selection to model policy and allowed a CAPTCHA-only sidecar to
become the provider for the main ETL graph.

Provider and model availability are operational states. An optional provider
outage must not terminate the bot, public API, scheduler or health endpoints.
The scheduler instead needs a deterministic decision for the affected run and
an operator-visible notification.

## Decision

1. Runtime configuration defines named provider profiles. A profile owns its
   adapter kind, base URL reference, credential reference, timeouts, retries,
   capabilities and provider-specific session reference. Secrets remain in
   environment variables or mounted secret storage and never enter graph YAML.
2. Every graph node that performs an LLM call resolves an explicit
   `(provider profile, model)` binding. A runtime default may be used only when
   the graph omits a binding; choosing a provider never changes a model and
   choosing a model never changes a provider.
3. CAPTCHA vision uses the same named-provider mechanism outside the ETL graph.
   The production CLIProxyAPI profile is initially authorized only for the
   `cliproxy_image` route with model `gemini-3.8-flash-high`.
4. Service startup validates syntax, references, capabilities and secret
   presence. Reachability, authentication, quota and model-catalog checks are
   readiness signals, not process-liveness requirements.
5. Before a scheduled run, the resolved graph is preflighted. An unavailable
   required binding prevents that run from starting source ingest. The service
   remains alive, records the reason and notifies the configured Telegram
   operator target.
6. Fallback is opt-in per binding and ordered explicitly. Automatic fallback
   to an arbitrary provider or model is forbidden. Every fallback emits an
   event containing the requested binding, selected binding and reason.
7. A provider failure after a run starts follows the binding's bounded retry
   policy. Quota exhaustion uses at most two retries separated by 30 minutes;
   authentication and unknown-model errors are not retried. After exhaustion,
   the affected run becomes blocked/degraded with a durable operator notice.
8. Liveness, readiness and run capability are separate:
   - liveness: the process can serve and schedule;
   - readiness: configured integrations are reachable;
   - run capability: every binding required by one resolved graph is usable or
     has an authorized fallback.

## Configuration shape

The canonical shape is provider-neutral:

```yaml
llm_providers:
  openai_main:
    kind: openai
    base_url_env: JOB_FTCH_OPENAI_BASE_URL
    api_key_env: JOB_FTCH_OPENAI_API_KEY
    capabilities: [text, structured_output]

  cliproxy_captcha:
    kind: openai_compatible
    base_url_env: JOB_FTCH_CLIPROXY_BASE_URL
    api_key_env: JOB_FTCH_CLIPROXY_API_KEY
    session_dir_env: JOB_FTCH_CLIPROXY_AUTH_DIR
    capabilities: [vision]

llm_defaults:
  provider: openai_main
  model: gpt-5.4-nano
```

A graph binding is explicit where it differs from the default:

```yaml
params:
  llm_provider: openai_main
  model: gpt-4.1-mini
  fallbacks: []
```

## Consequences

- (+) CLIProxyAPI can serve image CAPTCHA recognition without changing ETL
  extraction, relevance or ontology compilation.
- (+) Operator policy controls every fallback and every model transition.
- (+) A failed optional integration degrades readiness but does not stop the
  service.
- (+) A run cannot spend source-ingest resources when its required LLM binding
  is unusable.
- (-) Composition must resolve more than one provider instance and expose the
  resolved binding in telemetry.
- (-) Existing global gateway/model settings require a compatibility migration
  with warnings and an eventual removal date.

## Alternatives

- Keep one global gateway and add more overlays: rejected because transport and
  model policy remain coupled.
- Stop the process when any provider is unavailable: rejected because optional
  integrations must not remove scheduling, health or operator notification.
- Silently choose any available model: rejected because model changes affect
  extraction and relevance behavior.

## Related decisions

- [029 — LLM extraction points](029-llm-extraction-points.md)
- [071 — Durable delivery and observable runtime degradation](071-durable-delivery-and-runtime-degradation.md)
- [089 — OpenAI observability in OpenObserve](089-openai-observability-in-openobserve.md)
- [090 — Confirmed delivery](090-delivery-truth.md)
- [097 — Proxy, CAPTCHA and access budget](097-proxy-captcha-budget.md)
- [098 — Source coverage observability](098-source-coverage-observability.md)

## Specification

[Provider routing and production recovery specification](../specs/provider-routing-and-production-recovery.md).
