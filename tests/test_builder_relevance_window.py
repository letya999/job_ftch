from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.adapters.telegram_bot.main import _warn_on_dev_like_publish_settings
from job_ftch.application.builder import _log_relevance_window, build_nodes
from job_ftch.config import Settings
from job_ftch.domain import ProfileCatalog
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider


def test_log_relevance_window_reports_effective_window(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr("job_ftch.application.builder.structlog.get_logger", lambda *_args: logger)

    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "llm_relevance_low_threshold": 0.1,
            "llm_relevance_high_threshold": 0.99,
            "routing_accept_threshold": 0.55,
        }
    )

    _log_relevance_window(settings)

    logger.info.assert_called_once_with(
        "relevance_judge_window",
        low_threshold=0.1,
        high_threshold=0.99,
        routing_accept_threshold=0.55,
        expected_judge_coverage="judge-dominant",
    )


@pytest.mark.anyio
async def test_warn_on_dev_like_publish_settings_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("job_ftch.adapters.telegram_bot.main.logger", logger)

    runner = MagicMock()
    runner.tenant_ids.return_value = ["tenant_a"]
    runner.get_runtime.return_value = MagicMock(
        settings=Settings.model_validate(
            {
                "llm_backend": "heuristic",
                "log_level": "DEBUG",
                "tracing_capture_payloads": True,
            }
        )
    )
    runner.get_publish_channel = AsyncMock(return_value="@jobs_out")

    await _warn_on_dev_like_publish_settings(runner)

    logger.warning.assert_called_once_with(
        "dev-like settings with production publishing",
        tenant_id="tenant_a",
        publish_channel="@jobs_out",
    )


def test_build_nodes_keeps_presentable_text_out_of_terminal_pipeline() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "llm_presentable_enabled": True,
            "llm_relevance_max_per_run": 0,
            "relevance_backend": "keywords",
        }
    )

    _, _, nodes = build_nodes(
        settings,
        store=MagicMock(),
        llm=cast("LLMProvider", HeuristicLLMProvider()),
        job_group_store=MagicMock(),
        catalog=ProfileCatalog(),
    )

    assert not any(type(node).__name__ == "PresentableTextNode" for node in nodes)


def test_build_nodes_keeps_capable_presenter_in_post_accept_lane() -> None:
    class _CapableLLM:
        async def extract(self, text: str, schema: type[Any]) -> Any:
            del text
            return schema.model_validate({})

        async def classify(self, prompt: str, schema: type[Any]) -> Any:
            del prompt
            return schema.model_validate({})

        async def present(self, job_payload: str, schema: type[Any]) -> Any:
            del job_payload
            return schema.model_validate({})

        async def generate_text(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            temperature: float = 0.2,
        ) -> str:
            del system_prompt, user_prompt, temperature
            return "ok"

    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "llm_presentable_enabled": True,
            "llm_relevance_max_per_run": 0,
            "relevance_backend": "keywords",
        }
    )

    _, _, nodes = build_nodes(
        settings,
        store=MagicMock(),
        llm=cast("LLMProvider", _CapableLLM()),
        job_group_store=MagicMock(),
        catalog=ProfileCatalog(),
    )

    assert not any(type(node).__name__ == "PresentableTextNode" for node in nodes)
