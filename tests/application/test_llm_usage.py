from __future__ import annotations

from types import SimpleNamespace

from job_ftch.application.llm_usage import collect_llm_usage, record_provider_usage


def test_ledger_prices_provider_usage_with_cached_input() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40),
    )
    with collect_llm_usage() as ledger:
        record_provider_usage(model="gpt-4.1-mini", usage=usage, latency_ms=17)

    assert ledger.requests == 1
    assert ledger.tokens_in == 100
    assert ledger.cached_tokens_in == 40
    assert ledger.tokens_out == 20
    assert ledger.latency_ms == 17
    assert ledger.cost_usd == 0.00006
    assert ledger.cost_is_complete


def test_ledger_never_substitutes_pricing_for_unknown_model() -> None:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, prompt_tokens_details=None)
    with collect_llm_usage() as ledger:
        record_provider_usage(model="future-model", usage=usage, latency_ms=1)

    assert ledger.cost_usd == 0.0
    assert not ledger.cost_is_complete
    assert ledger.unknown_pricing_models == {"future-model"}
