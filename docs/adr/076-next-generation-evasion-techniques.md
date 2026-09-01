---
title: "076 — Next-generation anti-bot evasion techniques"
description: "**Status:** ACCEPTED"
updated: 2026-07-24
---
# 076 — Next-generation anti-bot evasion techniques

**Status:** ACCEPTED
**Date:** 2026-07-21
**Extends:** ADR-074, ADR-075, ADR-050, ADR-048

## Context

ADR-075 implemented five advanced evasion techniques (exponential fingerprint
space, temporal shaping, behavioral noise, session memory, distributed
simulation). These address fingerprint clustering, timing detection, and
traffic patterns. However, six additional evasion vectors remain unaddressed:

1. **Direct navigation pattern**: Users rarely navigate directly to career
   sites. They arrive via search engines, social networks, or email links.
   Direct navigation is a strong bot signal.

2. **Physical context inconsistency**: Anti-bot systems check consistency
   between timezone, locale, IP geolocation, and device properties. A user
   claiming "America/New_York" timezone with a German IP exit node is
   immediately flagged.

3. **Uniform behavioral patterns**: Current behavioral simulation uses fixed
   state transitions. Real users exhibit cognitive state changes (reading →
   scanning → thinking → distracted) that affect interaction patterns.

4. **Static fingerprint evolution**: Fingerprints are generated randomly but
   don't evolve based on success/failure. Anti-bot systems adapt; fingerprints
   should too.

5. **Temporal inconsistency**: Sessions lack temporal coherence. A user who
   visited 3 days ago should have consistent behavioral patterns with today's
   session. Anti-bot systems track returning users.

6. **Layered defense gaps**: Current evasion techniques operate independently.
   A coordinated multi-layer obfuscation strategy provides defense-in-depth.

These techniques require no ML models, no training data, and use only stdlib
dependencies. They build on ADR-075 infrastructure and integrate with the
existing adaptive bypass system (ADR-074).

## Decision

Implement six next-generation evasion techniques as composable infrastructure
components in `infrastructure/bypass/`:

### 1. Referrer Chain Forgery

**Location:** `infrastructure/bypass/referrer_chain.py`

Generate realistic referrer chains simulating organic navigation paths:

- Search engine referrers (Google, Bing, DuckDuckGo)
- Social media referrers (LinkedIn, Twitter, Facebook)
- Email referrers (Gmail, Outlook)
- Direct navigation (with low probability)
- Intermediate hops (2-4 referrers before target)

**Integration:** Extend `BypassContext` with `referrer_chain` attribute.
Integrate into `BrowserSessionBypass.open_page()` to set referrer headers
before navigation.

**Dependencies:** stdlib only (random, datetime, urllib).

### 2. Physical Context Emulation

**Location:** `infrastructure/bypass/physical_context.py`

Emulate complete physical context with consistency checks:

- Geolocation (latitude, longitude, accuracy)
- Timezone (auto-derived from geolocation)
- Device state (battery, charging, temperature)
- Network type (WiFi, 4G, 5G)
- Time of day (affects activity patterns)
- Locale/IP consistency validation

**Integration:** Extend `BrowserPersona` with `physical_context` attribute.
Integrate into `_generate_personas()` to generate consistent physical context.
Apply to `stealth_hardening.py` for timezone/locale spoofing.

**Dependencies:** stdlib only (dataclasses, datetime, math).

### 3. Cognitive State Machine

**Location:** `infrastructure/bypass/cognitive_state.py`

Model cognitive state transitions affecting behavioral parameters:

- READING: slow scroll, long pauses, precise mouse movement
- SCANNING: fast scroll, short pauses, quick mouse movement
- THINKING: no scroll, medium pauses, mouse hovering
- DISTRACTED: no interaction, long pauses
- TIRED: slower movement, more errors, shorter sessions

**Integration:** Extend `BehaviorSimBypass` with `cognitive_machine` attribute.
Integrate into `apply_page()` to adjust behavior based on cognitive state.

**Dependencies:** stdlib only (enum, dataclasses, random).

### 4. Evolutionary Fingerprint Breeding

**Location:** `infrastructure/bypass/fingerprint_evolution.py`

Use genetic algorithms to evolve fingerprints based on success rates:

- Population of fingerprints with success/failure tracking
- Fitness function based on bypass success rate
- Selection: tournament selection (top 20%)
- Crossover: single-point crossover of fingerprint attributes
- Mutation: random attribute changes (5% probability)
- Generational evolution with elitism

**Integration:** Extend `FingerprintGenerator` with `evolution` attribute.
Integrate into `AdaptiveBypassManager` to evolve fingerprints based on
success rates from `DomainIntel`.

**Dependencies:** stdlib only (random, dataclasses).

### 5. Temporal Consistency Graph

**Location:** `infrastructure/bypass/temporal_graph.py`

Build temporal graph of sessions to ensure consistency:

- Nodes: sessions (timestamp, persona_id, actions)
- Edges: temporal relationships (time delta, action similarity)
- Consistency checks: behavioral pattern similarity, timing distribution
- Anomaly detection: flag sessions deviating from persona history
- Exponential moving average for behavioral metrics

**Integration:** Extend `DomainIntel` with `temporal_graph` attribute.
Integrate into `AdaptiveBypassManager` to check consistency before actions.

**Dependencies:** stdlib only (dict-based graph, no external libraries).

### 6. Multi-Layer Fingerprint Obfuscation

**Location:** `infrastructure/bypass/multi_layer_obfuscation.py`

Orchestrate defense-in-depth across multiple obfuscation layers:

- Layer 1 (JS): 37 JS injection techniques (stealth_hardening.py)
- Layer 2 (Browser): Camoufox, Patchright (existing tiers)
- Layer 3 (Network): TLS fingerprint, HTTP/2 (curl_bypass.py)
- Layer 4 (Behavioral): Noise, timing (behavioral_noise.py, temporal_shaper.py)
- Layer 5 (Temporal): Consistency graph (temporal_graph.py)
- Layer 6 (Physical): Geo, device, network (physical_context.py)

**Integration:** Create `MultiLayerObfuscation` as orchestrator. Integrate
into `AdaptiveBypassManager.apply_page()` to apply all layers.

**Dependencies:** stdlib only (Protocol, dataclasses).

## Architecture

All six components follow the same pattern as ADR-075:

1. **Pure Python** — no external dependencies beyond stdlib
2. **Composable** — can be used independently or combined
3. **Registry-based** — self-register via `@register_bypass` if applicable
4. **Testable** — pure functions with deterministic seeds for testing
5. **Layer-compliant** — live in `infrastructure/bypass/`, no domain imports

```
infrastructure/bypass/
├── referrer_chain.py              # Referrer chain forgery
├── physical_context.py            # Physical context emulation
├── cognitive_state.py             # Cognitive state machine
├── fingerprint_evolution.py       # Evolutionary fingerprint breeding
├── temporal_graph.py              # Temporal consistency graph
└── multi_layer_obfuscation.py     # Multi-layer orchestration
```

## Integration Points

### BypassContext Extension

```python
class BypassContext:
    # ... existing fields ...
    referrer_chain: ReferrerChainGenerator | None = None
    physical_context: PhysicalContext | None = None
    cognitive_machine: CognitiveStateMachine | None = None
```

### Behavioral Simulation

```python
class BehaviorSimBypass:
    def __init__(self, ..., cognitive_machine: CognitiveStateMachine | None = None):
        self._cognitive_machine = cognitive_machine

    async def apply_page(self, page):
        if self._cognitive_machine:
            state = self._cognitive_machine.current_state
            params = self._cognitive_machine.get_behavior_params(state)
            await self._apply_cognitive_behavior(page, params)
```

### Adaptive Bypass Manager

```python
class AdaptiveBypassManager:
    async def apply_page(self, page):
        # Apply multi-layer obfuscation
        if self._multi_layer:
            await self._multi_layer.apply(page)

        # Evolve fingerprints based on success
        if self._fingerprint_evolution:
            self._fingerprint_evolution.evolve_population(success_rates)

        # Check temporal consistency
        if self._temporal_graph:
            if not self._temporal_graph.check_consistency(session):
                logger.warning("temporal_inconsistency_detected")
```

## Consequences

### Positive

- **Referrer chains** simulate organic navigation, evading direct-navigation detection
- **Physical context** ensures timezone/locale/IP consistency, evading geo-mismatch detection
- **Cognitive states** create realistic behavioral variations, evading pattern detection
- **Evolutionary breeding** adapts fingerprints to anti-bot changes, maintaining effectiveness
- **Temporal consistency** ensures returning-user coherence, evading session-isolation detection
- **Multi-layer defense** provides defense-in-depth, reducing single-point-of-failure risk

### Negative

- **Complexity**: Six new modules increase maintenance burden
- **Performance**: Temporal graph and evolutionary breeding add computational overhead
- **Storage**: Temporal graph persists session history (bounded by persona count)
- **Testing**: More integration points require comprehensive test coverage

### Neutral

- **No ML required**: All techniques are pure Python + statistics + genetic algorithms
- **No training data**: Uses mathematical models, not real datasets
- **Backward compatible**: All features are optional, existing code unchanged
- **Stdlib only**: No new external dependencies

## Testing Strategy

Each module has unit tests with deterministic seeds:

```python
def test_referrer_chain_realistic():
    gen = ReferrerChainGenerator(seed=42)
    chain = gen.generate_chain("https://example.com/jobs")
    assert len(chain) >= 2
    assert chain[-1].url == "https://example.com/jobs"


def test_physical_context_consistency():
    ctx = PhysicalContext(timezone="America/New_York", ip_country="US")
    assert ctx.is_consistent()


def test_cognitive_state_transitions():
    machine = CognitiveStateMachine(seed=42)
    machine.transition(UserEvent(type="long_pause"))
    assert machine.current_state == CognitiveState.DISTRACTED


def test_fingerprint_evolution_improves():
    evo = FingerprintEvolution(seed=42)
    population = [Fingerprint.random() for _ in range(100)]
    success_rates = [0.1] * 100
    evolved = evo.evolve(population, success_rates)
    assert evolved != population


def test_temporal_graph_consistency():
    graph = TemporalGraph()
    graph.add_session(Session(id="s1", persona_id="p1", timestamp=1000))
    graph.add_session(Session(id="s2", persona_id="p1", timestamp=2000))
    assert graph.check_consistency(Session(id="s3", persona_id="p1", timestamp=3000))


def test_multi_layer_applies_all():
    layers = [MockLayer(), MockLayer()]
    obfuscation = MultiLayerObfuscation(layers)
    await obfuscation.apply(context)
    assert all(layer.applied for layer in layers)
```

## Related ADR

- [ADR-074](074-adaptive-route-state-graph.md) — Adaptive route-state graph
- [ADR-075](075-advanced-anti-bot-evasion-techniques.md) — Advanced anti-bot evasion
- [ADR-050](050-browser-session-bypass-protocol.md) — Browser session bypass protocol
- [ADR-048](048-proxy-tier-in-adaptive-bypass-chain.md) — Proxy tier in adaptive chain
