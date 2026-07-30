---
title: "075 — Advanced anti-bot evasion techniques"
description: "**Status:** ACCEPTED"
updated: 2026-07-24
---
# 075 — Advanced anti-bot evasion techniques

**Status:** ACCEPTED
**Date:** 2026-07-20
**Extends:** ADR-074, ADR-050, ADR-048

## Context

The current bypass system (ADR-074) provides a comprehensive 6-tier adaptive
escalation chain with 37 JS injection techniques, behavioral simulation, and
persona management. However, several advanced evasion techniques remain
unimplemented:

1. **Fingerprint space limitation**: Current persona pool generates 20 fixed
   personas. Anti-bot systems can cluster traffic by fingerprint patterns.
   Exponential fingerprint space (10^24 combinations) makes clustering
   impossible.

2. **Uniform timing distribution**: Current delays use uniform random
   distribution. Real human behavior follows log-normal, Pareto, and gamma
   distributions. Anti-bot systems detect uniform distribution as bot-like.

3. **Deterministic behavior**: Bezier curves + Gaussian delays create
   deterministic patterns. Two bots with same parameters produce identical
   behavior → clustering. Microscopic noise injection makes each action unique.

4. **Session state loss**: Browser sessions are ephemeral. Returning users have
   cookies, localStorage, and visit history. Anti-bot systems check for
   "returning user" signals.

5. **Burst traffic patterns**: 1000 requests in 2 seconds ≠ 1000 requests over
   an hour. Real traffic follows Poisson distribution. Burst patterns trigger
   rate-limit and bot detection.

These techniques are proven in commercial solutions (Bright Data, Oxylabs) but
not implemented in open-source. They require no ML models, no training data,
and can be implemented quickly with high impact.

## Decision

Implement five advanced evasion techniques as composable infrastructure
components in `infrastructure/bypass/`:

### 1. Exponential Fingerprint Space

**Location:** `infrastructure/bypass/fingerprint_generator.py`

Generate millions of unique fingerprints on-the-fly via combinatorics:

- Canvas seed: 1000-99999 (99K variants)
- Audio seed: 1000-99999 (99K variants)
- WebGL renderer: 6 variants
- Font list: per-OS, 15-20 fonts → 2^15 combinations
- Screen size: 8 viewports × 3 screen heights → 24 variants
- Battery state: charging/discharging × 0-100% → 200 variants
- Hardware concurrency: 2/4/6/8/12/16 → 6 variants
- Device memory: 2/4/8/16 → 4 variants
- Font spacing seed: 1-999999 → 1M variants

**Total:** ~10^24 unique fingerprints per session.

**Integration:** Extend `BrowserPersona` with new attributes. Update
`_generate_personas()` to use `FingerprintGenerator`.

### 2. Temporal Request Shaping

**Location:** `infrastructure/bypass/temporal_shaper.py`

Replace uniform random delays with realistic statistical distributions:

- Reading time: LogNormal(μ=3.5, σ=0.8) — median ~30s, some 5 min
- Thinking time: Pareto(scale=5, shape=2) — heavy-tailed
- Scroll pause: Gamma(shape=2, scale=1.5) — pauses between scrolls
- Inter-arrival: Exponential(λ) — Poisson process

**Integration:** Compose with `BehaviorSimBypass.apply_page()`. Add
`TemporalShaper` as optional decorator.

### 3. Behavioral Noise Injection

**Location:** `infrastructure/bypass/behavioral_noise.py`

Add microscopic random deviations to every action:

- Mouse jitter: Normal(μ=0, σ=0.5px) — ±0.5px per coordinate
- Typing variance: Normal(μ=0, σ=20ms) — ±20ms per keystroke
- Scroll overshoot: Normal(μ=0, σ=5px) — ±5px overshoot
- Click pressure: Uniform(50-150ms) — click duration

**Integration:** Extend `BehaviorSimBypass` methods. Add noise as post-processing
step.

### 4. Session Memory Persistence

**Location:** `infrastructure/bypass/session_memory.py`

Persist session state between runs to simulate "returning user":

- Cookies (cf_clearance, dd_cookie, etc.)
- localStorage entries
- Visit count and last visit timestamp
- Behavioral profile (average reading time, scroll patterns)

**Storage:** `data/session_memory/{persona_id}.json`

**Integration:** Call `SessionMemory.capture_from_browser()` before browser
close. Call `SessionMemory.apply_to_browser()` after browser open.

### 5. Distributed Session Simulation

**Location:** `infrastructure/bypass/distributed_simulator.py`

Simulate realistic traffic distribution via Poisson process:

- Generate arrival times: Exponential(λ) distribution
- Cumulative sum = arrival times
- Execute tasks with Poisson timing

**Integration:** Use in pipeline-level orchestration for multi-URL scraping.
Optional decorator for `Source.fetch()`.

## Architecture

All five components follow the same pattern:

1. **Pure Python** — no external dependencies beyond stdlib + numpy (optional)
2. **Composable** — can be used independently or combined
3. **Registry-based** — self-register via `@register_bypass` if applicable
4. **Testable** — pure functions with deterministic seeds for testing
5. **Layer-compliant** — live in `infrastructure/bypass/`, no domain imports

```
infrastructure/bypass/
├── fingerprint_generator.py    # Exponential fingerprint space
├── temporal_shaper.py          # Statistical timing distributions
├── behavioral_noise.py         # Microscopic action noise
├── session_memory.py           # Persistent session state
└── distributed_simulator.py    # Poisson traffic simulation
```

## Integration Points

### Persona Extension

`BrowserPersona` gains new attributes:

```python
@dataclass(frozen=True, slots=True)
class BrowserPersona:
    # ... existing fields ...
    font_spacing_seed: int
    font_list: list[str]
    speech_voices: list[str]
    # New from ADR-075:
    fingerprint_hash: str  # Unique hash for exponential space
```

### Behavioral Simulation

`BehaviorSimBypass` integrates temporal shaping and noise:

```python
class BehaviorSimBypass:
    def __init__(self, ..., temporal_shaper: TemporalShaper | None = None):
        self._temporal_shaper = temporal_shaper
        self._noise = BehavioralNoise()

    async def apply_page(self, page):
        # Apply temporal shaping
        if self._temporal_shaper:
            await self._temporal_shaper.simulate_reading(page)

        # Apply noise to mouse movement
        await self._noise.add_jitter(mouse_trajectory)
```

### Session Handoff

`AdaptiveBypassManager` integrates session memory:

```python
async def open_page(self, config):
    # Restore session memory
    memory = SessionMemory(self._persona.name)
    await memory.apply_to_browser(context)

    try:
        yield page
    finally:
        # Capture session state
        await memory.capture_from_browser(context)
        memory.save()
```

## Consequences

### Positive

- **10^24 fingerprint space** makes clustering impossible
- **Realistic timing distributions** evade uniform-distribution detection
- **Unique behavioral patterns** prevent cross-bot correlation
- **Returning user signals** pass anti-bot "returning user" checks
- **Poisson traffic** mimics real user distribution

### Negative

- **Storage overhead**: Session memory files grow over time (bounded by persona count)
- **Complexity**: Five new modules increase maintenance burden
- **Testing**: Statistical distributions require seed-based deterministic testing

### Neutral

- **No ML required**: All techniques are pure Python + statistics
- **No training data**: Uses mathematical distributions, not real datasets
- **Backward compatible**: All features are optional, existing code unchanged

## Testing Strategy

Each module has unit tests with deterministic seeds:

```python
def test_fingerprint_generator_deterministic():
    gen = FingerprintGenerator(seed=42)
    fp1 = gen.generate()
    fp2 = gen.generate()
    assert fp1 != fp2  # Each call generates unique fingerprint

def test_temporal_shaper_log_normal():
    shaper = TemporalShaper(seed=42)
    times = [shaper.reading_time() for _ in range(1000)]
    assert median(times) ≈ 30  # LogNormal(μ=3.5, σ=0.8)

def test_behavioral_noise_jitter():
    noise = BehavioralNoise(seed=42)
    jittered = noise.add_jitter([(100, 200), (150, 250)])
    assert jittered != [(100, 200), (150, 250)]  # Noise added
```

## Related ADR

- [ADR-074](074-adaptive-route-state-graph.md) — Adaptive route-state graph
- [ADR-050](050-browser-session-bypass-protocol.md) — Browser session bypass protocol
- [ADR-048](048-proxy-tier-in-adaptive-bypass-chain.md) — Proxy tier in adaptive chain
- [ADR-037](037-adaptive-scraping-escalation-policy.md) — Adaptive scraping escalation
