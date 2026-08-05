from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
from job_ftch.adapters.telegram_bot.handlers.channel import handle_channel_entity
from job_ftch.adapters.telegram_bot.handlers.schedule import (
    _parse_interval_seconds,
    callback_set_schedule,
    cmd_schedule,
)
from job_ftch.adapters.telegram_bot.handlers.sources import cmd_sources
from job_ftch.adapters.telegram_bot.utils import safe_error_reply
from job_ftch.application.profile_parsing import (
    TUNED_PROFILE_WEIGHTS,
    TUNED_RELEVANCE_THRESHOLD,
)
from job_ftch.application.resume_extraction import add_example_to_profile
from job_ftch.domain import ManagedCandidateProfile, SearchProfile
from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile

pytestmark = pytest.mark.anyio


def _bot_config(*, admin: bool = True) -> TelegramBotConfig:
    return TelegramBotConfig(
        token="123456:test-token",
        admin_user_ids=(123,) if admin else (),
        allowed_user_ids=(123,),
    )


async def test_positive_command_adds_resume_shot() -> None:
    """Adding resume text via /positive must call add_example_to_profile with kind='positive'."""
    from job_ftch.adapters.telegram_bot.fsm.states import AddingExamples
    from job_ftch.adapters.telegram_bot.handlers.examples import handle_text_example

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runtime_mock = MagicMock()
    runtime_mock.embedding_provider = None
    runtime_mock.llm_provider = None  # disables enrichment
    runtime_mock.ontology_store = None
    runner.get_runtime = MagicMock(return_value=runtime_mock)

    profile = ManagedCandidateProfile(
        user_id="123",
        profile_id="user_123",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="123", display_name="User"),
            search_profiles=(
                SearchProfile(positive_example_texts=tuple(), negative_example_texts=tuple()),
            ),
        ),
    )
    runner.get_candidate_profile = AsyncMock(return_value=profile)
    runner.save_and_activate_candidate_profile = AsyncMock()

    message = MagicMock()
    message.text = (
        "This is a sufficiently long text for the resume example so it passes length check."
    )
    message.from_user.id = 123
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_state = AsyncMock(return_value=AddingExamples.positive.state)

    await handle_text_example(message=message, state=state, runner=runner)

    runner.save_and_activate_candidate_profile.assert_called_once()
    saved_profile = runner.save_and_activate_candidate_profile.call_args[0][1]
    assert len(saved_profile.profile.search_profiles[0].positive_example_texts) == 1


async def test_positive_job_command_adds_vacancy_shot() -> None:
    """Regression: /positive_job must persist text in positive_job_example_texts, not positive_example_texts."""
    profile = ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="Test"),
            search_profiles=(SearchProfile(profile_id="p1"),),
        ),
    )
    result = add_example_to_profile(profile, "Senior ML Engineer role", kind="positive_job")
    sp = result.profile.search_profiles[0]
    assert len(sp.positive_job_example_texts) == 1
    assert sp.positive_job_example_texts[0] == "Senior ML Engineer role"
    # Must NOT go into resume shots
    assert len(sp.positive_example_texts) == 0


async def test_negative_job_command_adds_negative_vacancy_shot() -> None:
    """Regression: /negative_job must persist text in negative_job_example_texts."""
    profile = ManagedCandidateProfile(
        user_id="u1",
        profile_id="p1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1", display_name="Test"),
            search_profiles=(SearchProfile(profile_id="p1"),),
        ),
    )
    result = add_example_to_profile(profile, "Accountant job unrelated", kind="negative_job")
    sp = result.profile.search_profiles[0]
    assert len(sp.negative_job_example_texts) == 1
    assert len(sp.negative_example_texts) == 0


async def test_vacancy_only_profile_uses_tuned_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_ftch.adapters.telegram_bot.fsm.states import AddingJobExamples
    from job_ftch.adapters.telegram_bot.handlers.examples import handle_job_text_example

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runtime_mock = MagicMock()
    runtime_mock.embedding_provider = None
    runtime_mock.llm_provider = None
    runtime_mock.ontology_store = None
    runner.get_runtime = MagicMock(return_value=runtime_mock)
    runner.get_candidate_profile = AsyncMock(return_value=None)
    runner.save_and_activate_candidate_profile = AsyncMock()

    monkeypatch.setattr(
        "job_ftch.application.shot_sync.remove_shot",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "job_ftch.application.shot_sync.add_shot",
        lambda **kwargs: None,
    )

    message = MagicMock()
    message.text = "Senior ML Engineer role with enough detail to pass validation and be saved."
    message.from_user.id = 123
    message.answer = AsyncMock()

    state = MagicMock()
    state.get_state = AsyncMock(return_value=AddingJobExamples.positive.state)

    await handle_job_text_example(message=message, state=state, runner=runner)

    saved_profile = runner.save_and_activate_candidate_profile.call_args[0][1]
    sp = saved_profile.profile.search_profiles[0]
    assert sp.relevance_threshold == TUNED_RELEVANCE_THRESHOLD
    assert sp.weights == TUNED_PROFILE_WEIGHTS


async def test_tenant_command_shows_current_selection() -> None:
    from job_ftch.adapters.telegram_bot.handlers.base import cmd_tenant

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="tenant_b")
    runner.tenant_ids = MagicMock(return_value=["tenant_a", "tenant_b"])

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    await cmd_tenant(message=message, runner=runner)

    text = message.answer.call_args.args[0]
    markup = message.answer.call_args.kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert "Текущий tenant: tenant_b" in text
    assert any(btn.text == "✓ tenant_b" for btn in buttons)
    assert any(btn.text == "tenant_a" for btn in buttons)


async def test_tenant_callback_persists_selected_tenant() -> None:
    from aiogram.types import Chat, Message

    from job_ftch.adapters.telegram_bot.handlers.base import TenantMenu, cb_set_tenant

    runner = MagicMock()
    runner.tenant_ids = MagicMock(return_value=["tenant_a", "tenant_b"])
    runner.set_selected_tenant_id = AsyncMock()

    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = Message.model_construct(
        message_id=1,
        date=None,
        chat=Chat.model_construct(id=1, type="private"),
    )
    object.__setattr__(callback.message, "edit_text", AsyncMock())

    await cb_set_tenant(callback=callback, callback_data=TenantMenu(index=1), runner=runner)

    runner.set_selected_tenant_id.assert_awaited_once_with("123", "tenant_b")
    callback.answer.assert_awaited_once_with("Tenant выбран: tenant_b")
    callback.message.edit_text.assert_awaited_once_with("Текущий tenant: tenant_b")


async def test_schedule_callback_stores_interval() -> None:
    """Pressing 1h schedule button must call runner.set_schedule_interval with 3600."""
    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.set_schedule_interval = AsyncMock()

    query = MagicMock()
    query.data = "sched:3600"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()

    query.from_user.id = 123
    state = MagicMock()
    state.set_state = AsyncMock()

    await callback_set_schedule(query=query, runner=runner, config=_bot_config(), state=state)

    runner.set_schedule_interval.assert_called_once_with("test_tenant", 3600)


async def test_schedule_callback_off_clears_interval() -> None:
    """Pressing 'off' must call runner.set_schedule_interval with None."""
    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.set_schedule_interval = AsyncMock()

    query = MagicMock()
    query.data = "sched:off"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()

    query.from_user.id = 123
    state = MagicMock()
    state.set_state = AsyncMock()

    await callback_set_schedule(query=query, runner=runner, config=_bot_config(), state=state)

    runner.set_schedule_interval.assert_called_once_with("test_tenant", None)


async def test_cmd_schedule_shows_last_scheduler_status() -> None:
    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_schedule_interval = AsyncMock(return_value=3600)
    runner.get_bot_scheduler_status = AsyncMock(
        return_value={
            "last_attempt_at": "2026-06-30T10:00:00+00:00",
            "last_success_at": "2026-06-30T10:05:00+00:00",
            "last_error": "run failed",
            "last_run_emitted": "7",
            "last_publish_attempt_at": None,
            "last_publish_success_at": "2026-06-30T10:06:00+00:00",
            "last_publish_error": "channel forbidden",
            "last_publish_sent": "2",
            "last_publish_skipped_at": "2026-06-30T14:05:00+00:00",
            "last_publish_skipped_reason": "no_new_jobs",
        }
    )

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    await cmd_schedule(message, runner, _bot_config())

    text = message.answer.call_args.args[0]
    assert "Последняя попытка:" in text
    assert "Найдено в последнем auto-run: 7" in text
    assert "Опубликовано в канал: 2" in text
    assert "Последняя публикация пропущена: no_new_jobs" in text
    assert "Ошибка run: run failed" in text
    assert "Ошибка публикации: channel forbidden" in text


async def test_channel_entity_stores_valid_username() -> None:
    """Valid @username must be stored via runner.set_publish_channel."""
    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.set_publish_channel = AsyncMock()
    store = MagicMock()
    store.set_run_state = AsyncMock()
    runner.get_runtime = MagicMock(return_value=MagicMock(store=store))

    message = MagicMock()
    message.text = "@mychannel"
    message.answer = AsyncMock()

    state = MagicMock()
    state.clear = AsyncMock()

    message.from_user.id = 123

    await handle_channel_entity(message=message, state=state, runner=runner, config=_bot_config())

    runner.set_publish_channel.assert_called_once_with("test_tenant", "@mychannel", user_id="123")
    store.set_run_state.assert_awaited_once()
    assert store.set_run_state.call_args.args[0] == "bot_scheduler:last_attempt_at"
    state.clear.assert_called_once()


async def test_channel_entity_rejects_invalid_format() -> None:
    """Non-channel text must trigger error message, not store anything."""
    runner = MagicMock()
    runner.set_publish_channel = AsyncMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")

    message = MagicMock()
    message.text = "not_a_channel"
    message.answer = AsyncMock()

    state = MagicMock()
    state.clear = AsyncMock()

    message.from_user.id = 123

    await handle_channel_entity(message=message, state=state, runner=runner, config=_bot_config())

    runner.set_publish_channel.assert_not_called()
    state.clear.assert_not_called()
    message.answer.assert_called_once()


async def test_channel_entity_rejects_when_bot_is_not_admin() -> None:
    runner = MagicMock()
    runner.set_publish_channel = AsyncMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")

    message = MagicMock()
    message.text = "@mychannel"
    message.answer = AsyncMock()
    message.from_user.id = 123

    state = MagicMock()
    state.clear = AsyncMock()

    bot = MagicMock()
    bot.get_chat = AsyncMock(return_value=MagicMock(id=-100123))
    bot.get_me = AsyncMock(return_value=MagicMock(id=777))
    bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))

    await handle_channel_entity(
        message=message,
        state=state,
        runner=runner,
        config=_bot_config(),
        bot=bot,
    )

    runner.set_publish_channel.assert_not_called()
    state.clear.assert_not_called()
    message.answer.assert_called_once()


async def test_channel_entity_stores_when_bot_can_post() -> None:
    runner = MagicMock()
    runner.set_publish_channel = AsyncMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    store = MagicMock()
    store.set_run_state = AsyncMock()
    runner.get_runtime = MagicMock(return_value=MagicMock(store=store))

    message = MagicMock()
    message.text = "@mychannel"
    message.answer = AsyncMock()
    message.from_user.id = 123

    state = MagicMock()
    state.clear = AsyncMock()

    bot = MagicMock()
    bot.get_chat = AsyncMock(return_value=MagicMock(id=-100123))
    bot.get_me = AsyncMock(return_value=MagicMock(id=777))
    bot.get_chat_member = AsyncMock(
        return_value=MagicMock(status="administrator", can_post_messages=True)
    )

    await handle_channel_entity(
        message=message,
        state=state,
        runner=runner,
        config=_bot_config(),
        bot=bot,
    )

    runner.set_publish_channel.assert_called_once_with("test_tenant", "@mychannel", user_id="123")
    store.set_run_state.assert_awaited_once()
    state.clear.assert_called_once()


def test_extract_source_inputs_accepts_type_hints_and_plain_urls() -> None:
    from job_ftch.adapters.telegram_bot.handlers.sources import _extract_source_inputs

    assert _extract_source_inputs(
        "https://example.com/jobs\n"
        "rss:https://example.com/feed.xml\n"
        "telegram_group:https://t.me/ml_jobs\n"
        "@another_jobs\n"
        "not_a_source"
    ) == [
        "https://example.com/jobs",
        "rss:https://example.com/feed.xml",
        "telegram_group:https://t.me/ml_jobs",
        "@another_jobs",
    ]


async def test_source_toggle_disables_selected_source() -> None:
    from job_ftch.adapters.telegram_bot.handlers.sources import (
        SourceItemAction,
        callback_toggle_source,
    )

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.list_sources = AsyncMock(
        return_value=[
            {
                "source_id": "career_site:example",
                "enabled": True,
                "spec": {"type": "career_site", "url": "https://example.com/jobs"},
            }
        ]
    )
    runner.disable_source = AsyncMock()

    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = None

    await callback_toggle_source(
        callback=callback,
        callback_data=SourceItemAction(action="disable", index=0),
        runner=runner,
        config=_bot_config(),
    )

    runner.disable_source.assert_called_once_with("test_tenant", "career_site:example")


async def test_cmd_sources_allows_admin_from_callback_override() -> None:
    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.list_sources = AsyncMock(return_value=[])

    message = MagicMock()
    message.from_user.id = 999999
    message.answer = AsyncMock()

    await cmd_sources(
        message=message,
        runner=runner,
        config=_bot_config(),
        user_id_override=123,
    )

    runner.list_sources.assert_called_once_with("test_tenant")


async def test_cmd_sources_shows_problem_status_and_error() -> None:
    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.list_sources = AsyncMock(
        return_value=[
            {
                "source_id": "career_site:hh_ai",
                "source_kind": "career_site",
                "locator": "https://hh.ru/search/vacancy?text=AI+Engineer",
                "enabled": True,
                "status": "failing",
                "failure_streak": 2,
                "last_failed": 1,
                "last_error": "403 Forbidden from upstream while fetching vacancies",
            }
        ]
    )

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    await cmd_sources(message, runner, _bot_config())

    sent_text = message.answer.call_args.args[0]
    assert "Статус источников:" in sent_text
    assert "⚠️ 1" in sent_text
    assert "403 Forbidden from upstream" in sent_text
    assert "streak=2" in sent_text
    answers = [call.args[0] for call in message.answer.call_args_list]
    assert "Команда доступна только администратору бота." not in answers


async def test_resume_photo_without_caption_asks_for_text() -> None:
    from job_ftch.adapters.telegram_bot.handlers.examples import handle_photo_example

    message = MagicMock()
    message.caption = None
    message.answer = AsyncMock()

    await handle_photo_example(message=message, state=MagicMock(), runner=MagicMock())

    assert "caption" in message.answer.call_args[0][0]


async def test_run_pipeline_uses_fallback_job_url_when_canonical_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    from job_ftch.domain.models import MatchDecision, PostType

    async def _alive(url: str | None) -> bool:
        return url == "https://example.com/fallback"

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.handlers.pipeline._url_is_alive", _alive)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender._render_with_layout",
        lambda job, *_args: f"<b>{job.title}</b> https://example.com/fallback",
    )

    runner = MagicMock()
    runtime = MagicMock()
    runtime.settings = MagicMock(
        bot_send_limit_per_run=5,
    )
    runner.get_runtime.return_value = runtime
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            fetched=1,
            extracted=1,
            duplicates=0,
            dropped=0,
            emitted=1,
            failed=0,
            drop_reasons={},
            source_failures=[],
            started_at=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
        )
    )
    runner.latest_jobs = AsyncMock(
        return_value=[
            MagicMock(
                title="AI Engineer",
                description="Job body",
                canonical_url=None,
                urls=["https://example.com/fallback"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            )
        ]
    )
    runner.get_publish_channel = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(side_effect=[status_msg, None])

    bot = MagicMock()

    await run_pipeline_for_chat(message, runner, bot)

    assert runner.latest_jobs.await_count == 1
    assert runner.latest_jobs.await_args.kwargs["since"] == datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    # one card send to chat after status message
    sent_card = message.answer.call_args_list[1].args[0]
    assert "<b>AI Engineer</b>" in sent_card
    assert "https://example.com/fallback" in sent_card
    assert "🔵" not in sent_card


async def test_run_pipeline_reports_channel_publish_outer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    from job_ftch.domain.models import MatchDecision, PostType

    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.handlers.pipeline._url_is_alive",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender._render_with_layout",
        lambda job, *_args: f"card:{job.title}",
    )
    runner = MagicMock()
    runtime = MagicMock()
    runtime.settings = MagicMock(
        bot_send_limit_per_run=5,
    )
    runner.get_runtime.return_value = runtime
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            fetched=1,
            extracted=1,
            duplicates=0,
            dropped=0,
            emitted=1,
            failed=0,
            drop_reasons={},
            source_failures=[],
        )
    )
    runner.latest_jobs = AsyncMock(
        return_value=[
            MagicMock(
                title="AI Engineer",
                description="Job body",
                canonical_url="https://example.com/job",
                urls=["https://example.com/job"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            )
        ]
    )
    runner.get_publish_channel = AsyncMock(side_effect=RuntimeError("channel lookup failed"))

    message = MagicMock()
    message.from_user.id = 123
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(side_effect=[status_msg, None, None])

    bot = MagicMock()

    await run_pipeline_for_chat(message, runner, bot)

    assert message.answer.call_args_list[-1].args[0].startswith("⚠️ Публикация")


async def test_run_pipeline_reports_partial_chat_delivery_and_publishes_only_delivered_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    from job_ftch.domain.models import MatchDecision, PostType

    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.handlers.pipeline._url_is_alive",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender._render_with_layout",
        lambda job, *_args: f"card:{job.title}",
    )
    runner = MagicMock()
    runtime = MagicMock()
    runtime.settings = MagicMock(
        bot_send_limit_per_run=5,
    )
    runner.get_runtime.return_value = runtime
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            fetched=2,
            extracted=2,
            duplicates=0,
            dropped=0,
            emitted=2,
            failed=0,
            drop_reasons={},
            source_failures=[],
        )
    )
    runner.latest_jobs = AsyncMock(
        return_value=[
            MagicMock(
                title="AI Engineer 1",
                description="Job body",
                canonical_url="https://example.com/job1",
                urls=["https://example.com/job1"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            ),
            MagicMock(
                title="AI Engineer 2",
                description="Job body",
                canonical_url="https://example.com/job2",
                urls=["https://example.com/job2"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            ),
        ]
    )
    runner.get_publish_channel = AsyncMock(return_value="@jobs_out")

    message = MagicMock()
    message.from_user.id = 123
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()

    sent_cards: list[str] = []

    async def _answer(text: str, **kwargs: object) -> object | None:
        del kwargs
        if text.startswith("🚀 Запускаю"):
            return status_msg
        if text.startswith("card:AI Engineer 1"):
            sent_cards.append(text)
            return None
        if text.startswith("card:AI Engineer 2"):
            raise RuntimeError("chat send failed")
        sent_cards.append(text)
        return None

    message.answer = AsyncMock(side_effect=_answer)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await run_pipeline_for_chat(message, runner, bot)

    status_text = status_msg.edit_text.call_args_list[-1].args[0]
    assert "✉️ Отправлено: 1" in status_text
    assert "Дальше отправка остановилась: chat send failed" in status_text
    bot.send_message.assert_called_once()
    assert bot.send_message.call_args.args[1] == "card:AI Engineer 1"


async def test_run_pipeline_backfills_past_dead_links_before_send_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    from job_ftch.domain.models import MatchDecision, PostType

    async def _alive(url: str | None) -> bool:
        return url == "https://example.com/job2"

    monkeypatch.setattr("job_ftch.adapters.telegram_bot.handlers.pipeline._url_is_alive", _alive)
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.sender._render_with_layout",
        lambda job, *_args: f"card:{job.title}",
    )
    runner = MagicMock()
    runtime = MagicMock()
    runtime.settings = MagicMock(
        bot_send_limit_per_run=1,
    )
    runner.get_runtime.return_value = runtime
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            fetched=2,
            extracted=2,
            duplicates=0,
            dropped=0,
            emitted=2,
            failed=0,
            drop_reasons={},
            source_failures=[],
        )
    )
    runner.latest_jobs = AsyncMock(
        return_value=[
            MagicMock(
                title="Dead Job",
                description="Job body",
                canonical_url="https://example.com/job1",
                urls=["https://example.com/job1"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            ),
            MagicMock(
                title="Live Job",
                description="Job body",
                canonical_url="https://example.com/job2",
                urls=["https://example.com/job2"],
                post_type=PostType.JOB_POSTING,
                routing_decision=MatchDecision.ACCEPT,
                quality_score=0.9,
                best_score=0.9,
                company="Acme",
                work_mode=None,
                location=None,
                compensation=None,
            ),
        ]
    )
    runner.get_publish_channel = AsyncMock(return_value=None)

    message = MagicMock()
    message.from_user.id = 123
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()

    async def _answer(text: str, **kwargs: object) -> object | None:
        del kwargs
        if text.startswith("🚀 Запускаю"):
            return status_msg
        return None

    message.answer = AsyncMock(side_effect=_answer)
    bot = MagicMock()

    await run_pipeline_for_chat(message, runner, bot)

    sent_texts = [call.args[0] for call in message.answer.call_args_list]
    assert "card:Live Job" in sent_texts
    assert "card:Dead Job" not in sent_texts
    status_text = status_msg.edit_text.call_args_list[-1].args[0]
    assert "✉️ Отправлено: 1" in status_text


async def test_run_pipeline_reports_persistence_contract_violation() -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat

    runner = MagicMock()
    runner.get_runtime.return_value = MagicMock(settings=MagicMock(bot_send_limit_per_run=5))
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            source_run_id="run-123",
            graph_hash="graph-456",
            started_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
            fetched=10,
            extracted=8,
            duplicates=0,
            dropped=2,
            emitted=3,
            failed=0,
            drop_reasons={},
            source_failures=[],
        )
    )
    runner.latest_jobs = AsyncMock(return_value=[])

    status_msg = MagicMock(edit_text=AsyncMock())
    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock(return_value=status_msg)

    await run_pipeline_for_chat(message, runner, MagicMock())

    status_text = status_msg.edit_text.call_args_list[-1].args[0]
    assert "приняты пайплайном, но не появились в выдаче" in status_text
    assert "/clear" not in status_text
    assert runner.latest_jobs.await_count == 1


async def test_run_pipeline_reports_seen_items_and_recent_auto_publish() -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.get_bot_scheduler_status = AsyncMock(
        return_value={
            "last_publish_sent": "22",
            "last_publish_success_at": "2026-08-01T20:06:56.774182+00:00",
        }
    )
    runner.run_tenant = AsyncMock(
        return_value=MagicMock(
            source_run_id="run-123",
            graph_hash="graph-456",
            fetched=1446,
            extracted=3,
            duplicates=0,
            dropped=1612,
            emitted=0,
            failed=0,
            drop_reasons={
                "already_seen": 682,
                "duplicate_content": 909,
                "low_relevance_prefilter": 21,
            },
            source_failures=[],
            llm_cost_usd=0.002192,
            llm_usage_requests=3,
            llm_cost_is_complete=True,
        )
    )

    status_msg = MagicMock(edit_text=AsyncMock())
    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock(return_value=status_msg)

    await run_pipeline_for_chat(message, runner, MagicMock())

    status_text = status_msg.edit_text.call_args_list[-1].args[0]
    assert "👁 Уже видели:       1591" in status_text
    assert "📉 Низкая релевантность: 21" in status_text
    assert "Почти всё уже есть в базе" in status_text
    assert "автопубликация уже проходила" in status_text
    assert "отправлено 22" in status_text
    assert "2026-08-01 20:06:56.774182+00:00" in status_text


def test_publish_candidate_fetch_limit_uses_wide_pool() -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import publish_candidate_fetch_limit

    assert publish_candidate_fetch_limit(1) == 100
    assert publish_candidate_fetch_limit(5) == 100
    assert publish_candidate_fetch_limit(15) == 300


def test_format_source_line_surfaces_browser_requirement_hint() -> None:
    from job_ftch.adapters.telegram_bot.handlers.sources import _format_source_line

    line = _format_source_line(
        {
            "source_kind": "career_site",
            "source_name": "ozon",
            "source_id": "career_site:ozon",
            "locator": "https://ozon.tech/vacancies",
            "status": "failing",
            "requirements": {
                "browser_required": True,
                "browser_setup_hint": "Requires Playwright + Chromium in the runtime image/environment.",
            },
        },
        index=3,
    )

    assert "browser" in line
    assert "Playwright + Chromium" in line


def test_format_source_line_surfaces_recommended_monitors() -> None:
    from job_ftch.adapters.telegram_bot.handlers.sources import _format_source_line

    line = _format_source_line(
        {
            "source_kind": "career_site",
            "source_name": "hh_ai",
            "source_id": "career_site:hh_ai",
            "locator": "https://hh.ru/search/vacancy?text=AI",
            "status": "pending",
            "assessment": {
                "recommended_monitors": ["api_sniffer", "dom"],
            },
        },
        index=1,
    )

    assert "api_sniffer -> dom" in line


def test_parse_interval_seconds_accepts_suffixes() -> None:
    assert _parse_interval_seconds("900") == 900
    assert _parse_interval_seconds("15m") == 900
    assert _parse_interval_seconds("2h") == 7200
    assert _parse_interval_seconds("1d") == 86400


async def test_handler_exception_gives_generic_reply_not_stacktrace() -> None:
    """Handler exceptions must NOT leak str(e) to users — generic message only."""
    message = MagicMock()
    message.answer = AsyncMock()

    exc = ValueError("secret internal detail: postgres://user:pass@host/db")
    await safe_error_reply(message, exc, "test_context")

    call_args = message.answer.call_args[0][0]
    assert "secret internal detail" not in call_args
    assert "postgres://" not in call_args
    assert "Произошла ошибка" in call_args


def test_build_resumes_menu_has_three_buttons_no_vacancy_actions() -> None:
    """_build_resumes_menu produces only resume-related buttons (no vacancy cross-contamination)."""
    from job_ftch.adapters.telegram_bot.handlers.examples import _build_resumes_menu

    builder = _build_resumes_menu(pos=2, neg=1)
    markup = builder.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 3
    text_set = {btn.text for btn in buttons}
    assert "📗 Подходящие (2)" in text_set
    assert "📕 Неподходящие (1)" in text_set
    assert "🗑 Удалить все резюме" in text_set
    # MUST NOT contain vacancy buttons
    assert all("Ваканси" not in btn.text for btn in buttons)


def test_build_vacancies_menu_has_three_buttons_no_resume_actions() -> None:
    """_build_vacancies_menu produces only vacancy-related buttons (no resume cross-contamination)."""
    from job_ftch.adapters.telegram_bot.handlers.examples import _build_vacancies_menu

    builder = _build_vacancies_menu(pos_job=4, neg_job=2)
    markup = builder.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 3
    text_set = {btn.text for btn in buttons}
    assert "💼 Подходящие (4)" in text_set
    assert "📄 Неподходящие (2)" in text_set
    assert "🗑 Удалить все вакансии" in text_set
    # MUST NOT contain resume buttons
    assert all("Резюме" not in btn.text for btn in buttons)


def test_build_examples_launcher_has_two_section_buttons() -> None:
    """_build_examples_launcher produces exactly 2 buttons: resumes + vacancies."""
    from job_ftch.adapters.telegram_bot.handlers.examples import _build_examples_launcher

    builder = _build_examples_launcher(pos=3, neg=1, pos_job=2, neg_job=4)
    markup = builder.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 2
    text_set = {btn.text for btn in buttons}
    assert "📋 Резюме (3+ / 1−)" in text_set
    assert "💼 Вакансии (2+ / 4−)" in text_set


async def test_cmd_resumes_shows_resume_buttons_when_present() -> None:
    """/resumes shows resume section menu when resume shots exist."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_resumes

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(
                    SearchProfile(
                        profile_id="p1",
                        positive_example_texts=("resume A long text desc with",),
                        negative_example_texts=("resume B unrelated accounting role",),
                    ),
                ),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_resumes(message=message, runner=runner)

    call_args = message.answer.call_args
    assert "reply_markup" in call_args.kwargs
    assert "Резюме (1+ / 1−)" in call_args[0][0]
    assert "/positive" in call_args[0][0]


async def test_cmd_resumes_shows_hint_when_empty() -> None:
    """/resumes shows a help message when there are no resume shots yet."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_resumes

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(
                    SearchProfile(
                        profile_id="p1",
                        positive_example_texts=(),
                        negative_example_texts=(),
                        positive_job_example_texts=("some vacancy",),
                    ),
                ),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_resumes(message=message, runner=runner)

    call_text = message.answer.call_args[0][0]
    # empty resumes → hint, not buttons
    assert "reply_markup" not in message.answer.call_args.kwargs
    assert "Резюме ещё нет" in call_text
    assert "/positive" in call_text


async def test_cmd_vacancies_shows_buttons_when_vacancies_present() -> None:
    """/vacancies shows vacancy section menu when vacancy shots exist."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_vacancies

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(
                    SearchProfile(
                        profile_id="p1",
                        positive_job_example_texts=("job1 long text desc with eng",),
                        negative_job_example_texts=("job2 unrelated accounting role",),
                    ),
                ),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_vacancies(message=message, runner=runner)

    call_args = message.answer.call_args
    assert "reply_markup" in call_args.kwargs
    assert "Вакансии (1+ / 1−)" in call_args[0][0]
    assert "/positive_job" in call_args[0][0]


async def test_cmd_vacancies_shows_hint_when_empty() -> None:
    """/vacancies shows a help message when there are no vacancy shots yet."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_vacancies

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(
                    SearchProfile(
                        profile_id="p1",
                        positive_example_texts=("resume text desc with eng",),
                        positive_job_example_texts=(),
                        negative_job_example_texts=(),
                    ),
                ),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_vacancies(message=message, runner=runner)

    call_text = message.answer.call_args[0][0]
    assert "reply_markup" not in message.answer.call_args.kwargs
    assert "Вакансий ещё нет" in call_text
    assert "/positive_job" in call_text


async def test_cmd_examples_launcher_with_only_resume_present() -> None:
    """/examples launcher must show both buttons (resumes + vacancies) even when vacancies are 0."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_examples

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(
                    SearchProfile(
                        profile_id="p1",
                        positive_example_texts=("resume text desc with eng",),
                        positive_job_example_texts=(),
                        negative_job_example_texts=(),
                    ),
                ),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_examples(message=message, runner=runner)

    call_args = message.answer.call_args
    assert "reply_markup" in call_args.kwargs
    text = call_args[0][0]
    assert "Ваши примеры" in text
    markup = call_args.kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 2
    btn_texts = {btn.text for btn in buttons}
    assert any("📋 Резюме" in t for t in btn_texts)
    assert any("💼 Вакансии" in t for t in btn_texts)


async def test_cmd_examples_launcher_when_all_empty_shows_hint() -> None:
    """/examples with zero shots shows a help message listing all 4 add commands."""
    from job_ftch.adapters.telegram_bot.handlers.examples import cmd_examples

    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="test_tenant")
    runner.tenant_ids = MagicMock(return_value=["test_tenant"])
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="u1",
            profile_id="p1",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="u1", display_name="T"),
                search_profiles=(SearchProfile(profile_id="p1"),),
            ),
        )
    )

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()

    await cmd_examples(message=message, runner=runner)

    text = message.answer.call_args[0][0]
    assert "Примеров ещё нет" in text
    assert "/positive" in text
    assert "/positive_job" in text


async def test_callback_del_confirm_vacancies_keeps_resume_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram.types import Chat, Message

    from job_ftch.adapters.telegram_bot.handlers.examples import callback_del_confirm_vacancies

    class _EmbedProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(texts)), 1.0] for _ in texts]

    monkeypatch.setattr("job_ftch.application.shot_sync.remove_user_shots", lambda **kwargs: None)
    monkeypatch.setattr(
        "job_ftch.application.shot_sync.sync_profile_to_shot_store",
        AsyncMock(return_value=None),
    )

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="tenant_a")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="123",
            profile_id="user_123",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="123", display_name="User"),
                search_profiles=(
                    SearchProfile(
                        profile_id="user_123",
                        positive_example_texts=("resume keeps profile active",),
                        positive_job_example_texts=("vacancy to delete",),
                        negative_embedding_vectors=((7.0, 7.0),),
                        embedding_vector=(9.0, 9.0),
                    ),
                ),
            ),
        )
    )
    runtime = MagicMock()
    runtime.embedding_provider = _EmbedProvider()
    runner.get_runtime = MagicMock(return_value=runtime)
    runner.save_and_activate_candidate_profile = AsyncMock()

    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = Message.model_construct(
        message_id=1,
        date=None,
        chat=Chat.model_construct(id=1, type="private"),
    )
    object.__setattr__(callback.message, "edit_text", AsyncMock())

    await callback_del_confirm_vacancies(callback=callback, runner=runner)

    saved_profile = runner.save_and_activate_candidate_profile.call_args.args[1]
    sp = saved_profile.profile.search_profiles[0]
    assert sp.positive_job_example_texts == ()
    assert sp.embedding_vector is not None
    assert sp.negative_embedding_vectors == ()


async def test_callback_del_one_uses_save_and_activate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.adapters.telegram_bot.handlers.examples import ExampleNav, callback_del_one

    monkeypatch.setattr("job_ftch.application.shot_sync.remove_shot", lambda **kwargs: None)

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="tenant_a")
    runner.get_candidate_profile = AsyncMock(
        return_value=ManagedCandidateProfile(
            user_id="123",
            profile_id="user_123",
            profile=CandidateProfile(
                identity=CandidateIdentity(candidate_id="123", display_name="User"),
                search_profiles=(
                    SearchProfile(
                        profile_id="user_123",
                        positive_example_texts=("resume A", "resume B"),
                    ),
                ),
            ),
        )
    )
    runtime = MagicMock()
    runtime.embedding_provider = None
    runner.get_runtime = MagicMock(return_value=runtime)
    runner.save_candidate_profile = AsyncMock()
    runner.save_and_activate_candidate_profile = AsyncMock()

    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = None

    await callback_del_one(
        callback=callback,
        callback_data=ExampleNav(action="del_one_pos", idx=0),
        runner=runner,
    )

    runner.save_and_activate_candidate_profile.assert_awaited_once()
    runner.save_candidate_profile.assert_not_called()


async def test_clear_refuses_while_run_is_active() -> None:
    """Regression: /clear wiped dedup+groups+vectors under a live /run (2026-07-19 incident)."""
    from job_ftch.adapters.telegram_bot.handlers.pipeline import _active_runs, cmd_clear

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="test_tenant")
    runner.clear_run_data = AsyncMock(
        return_value={"dedup_records": 1, "jobs": 4, "job_groups": 2, "vectors": 3}
    )

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    _active_runs.add("test_tenant")
    try:
        await cmd_clear(message=message, runner=runner, bot=MagicMock())
    finally:
        _active_runs.discard("test_tenant")

    runner.clear_run_data.assert_not_called()
    assert "Пайплайн сейчас работает" in message.answer.call_args[0][0]


async def test_clear_runs_when_no_active_run() -> None:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import cmd_clear

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="idle_tenant")
    runner.clear_run_data = AsyncMock(
        return_value={
            "dedup_records": 1,
            "jobs": 4,
            "job_groups": 2,
            "vectors": 3,
            "snapshots": 4,
            "source_ingest_states": 5,
            "outbox": 6,
        }
    )
    runner.get_publish_channel = AsyncMock(return_value=None)
    runtime = MagicMock()
    runtime.settings = None
    runner.get_runtime = MagicMock(return_value=runtime)

    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock()

    await cmd_clear(message=message, runner=runner, bot=MagicMock())

    runner.clear_run_data.assert_awaited_once()
    reply = message.answer.call_args[0][0]
    assert "джобов 4" in reply
    assert "снапшотов 4" in reply
    assert "source state 5" in reply


async def test_run_reports_skipped_when_tenant_lock_held() -> None:
    """Regression: an empty summary from a locked tenant was shown as 'nothing found'."""
    from job_ftch.adapters.telegram_bot.handlers.pipeline import run_pipeline_for_chat
    from job_ftch.application.pipeline import RunSummary

    summary = RunSummary()
    summary.skipped_already_active = True

    runner = MagicMock()
    runner.get_selected_tenant_id = AsyncMock(return_value="locked_tenant")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.run_tenant = AsyncMock(return_value=summary)
    runner.latest_jobs = AsyncMock(return_value=[])

    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message = MagicMock()
    message.from_user.id = 123
    message.answer = AsyncMock(return_value=status_msg)

    await run_pipeline_for_chat(message=message, runner=runner, bot=MagicMock())

    runner.latest_jobs.assert_not_called()
    assert "уже идёт" in status_msg.edit_text.call_args[0][0]
