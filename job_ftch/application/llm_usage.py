"""Run-scoped accounting for usage returned by LLM providers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from job_ftch.application.llm_pricing import PRICING_VERSION, estimate_cost

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(slots=True)
class LLMUsageLedger:
    """Accumulates only usage actually returned by successful provider calls."""

    requests: int = 0
    tokens_in: int = 0
    cached_tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    unknown_pricing_models: set[str] = field(default_factory=set)

    def record(
        self,
        *,
        model: str,
        tokens_in: int,
        cached_tokens_in: int,
        tokens_out: int,
        latency_ms: int,
    ) -> None:
        self.requests += 1
        self.tokens_in += max(tokens_in, 0)
        self.cached_tokens_in += min(max(cached_tokens_in, 0), max(tokens_in, 0))
        self.tokens_out += max(tokens_out, 0)
        self.latency_ms += max(latency_ms, 0)
        cost = estimate_cost(
            model,
            tokens_in,
            tokens_out,
            cached_tokens_in=cached_tokens_in,
        )
        if cost is None:
            self.unknown_pricing_models.add(model)
        else:
            self.cost_usd += cost

    @property
    def cost_is_complete(self) -> bool:
        return not self.unknown_pricing_models


_ACTIVE_LEDGER: ContextVar[LLMUsageLedger | None] = ContextVar("job_ftch_llm_usage", default=None)


@contextmanager
def collect_llm_usage() -> Iterator[LLMUsageLedger]:
    ledger = LLMUsageLedger()
    token: Token[LLMUsageLedger | None] = _ACTIVE_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _ACTIVE_LEDGER.reset(token)


def record_provider_usage(*, model: str, usage: object, latency_ms: int) -> None:
    """Record an OpenAI-compatible ``usage`` object when a run is active."""
    ledger = _ACTIVE_LEDGER.get()
    if ledger is None or usage is None:
        return
    tokens_in = _int_field(usage, "prompt_tokens")
    tokens_out = _int_field(usage, "completion_tokens")
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens_in = _int_field(prompt_details, "cached_tokens")
    # A response without usage is not billable evidence and must not make the
    # run look complete.
    if tokens_in is None or tokens_out is None:
        ledger.unknown_pricing_models.add(model)
        return
    ledger.record(
        model=model,
        tokens_in=tokens_in,
        cached_tokens_in=cached_tokens_in or 0,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )


def pricing_version() -> str:
    return PRICING_VERSION


def _int_field(value: object, field_name: str) -> int | None:
    field = getattr(value, field_name, None)
    return field if isinstance(field, int) else None
