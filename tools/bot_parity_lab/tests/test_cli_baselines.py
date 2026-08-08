from __future__ import annotations

from paritylab.clients import ADAPTERS


def test_automated_cross_engine_baseline_adapters_are_registered() -> None:
    assert {"playwright-chrome-channel", "playwright-firefox", "playwright-webkit"} <= set(
        ADAPTERS
    )
