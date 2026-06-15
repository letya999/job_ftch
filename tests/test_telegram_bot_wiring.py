"""Wiring smoke tests for the aiogram-based Telegram bot adapter.

These verify that the single living bot implementation (World A: aiogram
Dispatcher + routers + middlewares) assembles correctly and embeds the
job_ftch library exclusively through ``TenantRunner``. They are skipped when
the optional ``telegram`` extra (aiogram) is not installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("aiogram")

from adapters.telegram_bot.config import TelegramBotConfig
from adapters.telegram_bot.main import build_bot, build_dispatcher
from adapters.telegram_bot.handlers import base, pipeline, sources


@pytest.fixture(autouse=True)
def _reset_routers() -> None:
    """Reset parent_router for all global routers to allow re-attachment in tests."""
    for router in [base.router, pipeline.router, sources.router]:
        router._parent_router = None


def _config() -> TelegramBotConfig:
    return TelegramBotConfig(
        token="123456:test-token",
        allowed_user_ids=(1,),
        admin_user_ids=(1,),
        rate_limit_seconds=0.0,
    )


def test_build_bot_uses_configured_token() -> None:
    bot = build_bot(_config())
    assert bot.token == "123456:test-token"


def test_build_dispatcher_registers_all_routers() -> None:
    runner = MagicMock()
    dispatcher = build_dispatcher(runner=runner, config=_config())

    router_names = {router.name for router in dispatcher.sub_routers}
    assert {"base", "pipeline", "sources"} <= router_names


def test_build_dispatcher_passes_optional_dependencies() -> None:
    runner = MagicMock()
    embedding_provider = MagicMock()
    reranker = MagicMock()

    dispatcher = build_dispatcher(
        runner=runner,
        config=_config(),
        embedding_provider=embedding_provider,
        reranker=reranker,
    )

    # The dispatcher must be usable (routers attached) and not raise on assembly.
    assert dispatcher.sub_routers
