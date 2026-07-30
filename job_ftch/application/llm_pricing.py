"""Versioned public pricing used with provider-reported LLM usage."""

from __future__ import annotations

PRICING_VERSION = "openai-public-2026-07-16"

# (input, cached input, output), USD per million tokens.
LLM_COSTS: dict[str, tuple[float, float, float]] = {
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4": (2.50, 0.25, 15.00),
}


def estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    cached_tokens_in: int = 0,
) -> float | None:
    """Return standard-price cost, or ``None`` when model pricing is unknown."""
    rates = _rates_for_model(model)
    if rates is None:
        return None
    input_rate, cached_input_rate, output_rate = rates
    cached = min(max(cached_tokens_in, 0), max(tokens_in, 0))
    uncached = max(tokens_in, 0) - cached
    return (
        uncached * input_rate + cached * cached_input_rate + max(tokens_out, 0) * output_rate
    ) / 1_000_000


def _rates_for_model(model: str) -> tuple[float, float, float] | None:
    normalized = model.strip().lower()
    if normalized in LLM_COSTS:
        return LLM_COSTS[normalized]
    for name in sorted(LLM_COSTS, key=len, reverse=True):
        if normalized.startswith(f"{name}-"):
            return LLM_COSTS[name]
    return None
