"""The feedback button collects evidence; it never changes a published vacancy."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from job_ftch.adapters.telegram_bot.config import TelegramBotConfig
from job_ftch.adapters.telegram_bot.handlers.feedback import (
    FeedbackAction,
    FeedbackAdminAction,
    _admin_markup,
    build_feedback_markup,
    callback_feedback_admin,
    callback_vacancy_feedback,
    cmd_feedback,
    render_summary,
)
from job_ftch.application.vacancy_feedback import (
    get_feedback_audience,
    load_feedback,
    set_feedback_audience,
    summarize_feedback,
)
from job_ftch.domain.feedback import FeedbackAudience

pytestmark = pytest.mark.anyio


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_run_state(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.values[key] = value


def _bot_config(*, admin: bool = True) -> TelegramBotConfig:
    return TelegramBotConfig(
        token="123456:test-token",
        admin_user_ids=(123,) if admin else (),
        allowed_user_ids=(123,),
    )


def _runner(store: _Store, *, job: object | None = None) -> MagicMock:
    if job is not None:
        # The button carries a prefix, so enrichment expands it through the publish
        # ledger the channel already writes.
        store.values["bot_publish:sent_ids"] = json.dumps([_REAL_ID])
    runner = MagicMock()
    runner.default_tenant_id = MagicMock(return_value="ai_jobs")
    runner.get_selected_tenant_id = AsyncMock(return_value="ai_jobs")
    runner.get_runtime = MagicMock(return_value=SimpleNamespace(store=store))
    runner.get_job = AsyncMock(return_value=job)
    return runner


def _callback(data: str, *, user_id: int = 123) -> MagicMock:
    callback = MagicMock()
    callback.from_user = SimpleNamespace(id=user_id)
    callback.data = data
    callback.answer = AsyncMock()
    # spec=Message so the handler's isinstance guard behaves as it does in aiogram.
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    return callback


# Real publish ids are 64-char SHA-256 hex. The first version of these tests used short
# stand-ins and so never noticed that the full id does not fit in callback_data.
_REAL_ID = "c9d34f3a967a127ec5804211042873deacd65d43ea26aa28a179d2720a53882d"
_REAL_PREFIX = _REAL_ID[:16]


def _job(job_id: str = _REAL_ID) -> SimpleNamespace:
    return SimpleNamespace(
        group_id=job_id,
        title="ML developer in geolocation team",
        canonical_url="https://example.com/1",
        source_name="yandex",
        description_clean="Train ranking models for map search.",
    )


def test_markup_carries_a_prefix_of_a_real_publish_id() -> None:
    markup = build_feedback_markup(_job())

    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == FeedbackAction(job_id=_REAL_PREFIX).pack()


def test_markup_fits_the_telegram_callback_budget() -> None:
    """A 64-char SHA-256 id plus the prefix overflows the 64-byte cap."""
    markup = build_feedback_markup(_job())

    assert markup is not None
    payload = markup.inline_keyboard[0][0].callback_data
    assert payload is not None
    assert len(payload.encode()) <= 64


def test_markup_is_omitted_without_a_stable_id() -> None:
    """An unattributable button is worse than none: it looks like it worked."""
    assert build_feedback_markup(SimpleNamespace(title="x")) is None


def test_markup_survives_an_unusually_long_id() -> None:
    """Truncation, not rejection: a long id must still yield a usable button."""
    markup = build_feedback_markup(_job("y" * 200))

    assert markup is not None
    payload = markup.inline_keyboard[0][0].callback_data
    assert payload is not None
    assert len(payload.encode()) <= 64


def test_admin_markup_callback_data_uses_aiogram_safe_values() -> None:
    markup = _admin_markup(FeedbackAudience.OFF)

    payloads = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]

    assert "fbadm:set_off" in payloads
    assert "fbadm:set_admin" in payloads
    assert "fbadm:set_all" in payloads


async def test_a_press_records_one_verdict() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}"), FeedbackAction(job_id=_REAL_PREFIX), runner, _bot_config()
    )

    records = await load_feedback(store, "ai_jobs")
    assert len(records) == 1
    assert records[0].title == "ML developer in geolocation team"
    assert records[0].source_name == "yandex"
    assert records[0].excerpt.startswith("Train ranking models")


async def test_a_second_press_by_the_same_reader_is_idempotent() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())

    for _ in range(3):
        await callback_vacancy_feedback(
            _callback(f"fbk:{_REAL_PREFIX}"),
            FeedbackAction(job_id=_REAL_PREFIX),
            runner,
            _bot_config(),
        )

    assert len(await load_feedback(store, "ai_jobs")) == 1


async def test_presses_are_ignored_while_collection_is_off() -> None:
    store = _Store()
    runner = _runner(store, job=_job())

    callback = _callback(f"fbk:{_REAL_PREFIX}")
    await callback_vacancy_feedback(
        callback, FeedbackAction(job_id=_REAL_PREFIX), runner, _bot_config()
    )

    assert await load_feedback(store, "ai_jobs") == []
    callback.answer.assert_awaited()


async def test_a_missing_job_still_records_the_verdict() -> None:
    """Losing the reader's correction because the job row expired would be worse."""
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=None)

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}"), FeedbackAction(job_id=_REAL_PREFIX), runner, _bot_config()
    )

    records = await load_feedback(store, "ai_jobs")
    assert len(records) == 1
    assert records[0].title == ""


async def test_all_mode_lets_a_plain_reader_flag_a_card() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}", user_id=999),
        FeedbackAction(job_id=_REAL_PREFIX),
        runner,
        _bot_config(),
    )

    assert len(await load_feedback(store, "ai_jobs")) == 1


@pytest.mark.parametrize("audience", list(FeedbackAudience))
async def test_admin_selects_the_audience(audience: FeedbackAudience) -> None:
    store = _Store()
    runner = _runner(store)

    await callback_feedback_admin(
        _callback(f"fbadm:set:{audience.value}"),
        FeedbackAdminAction(action=f"set:{audience.value}"),
        runner,
        _bot_config(),
    )

    assert await get_feedback_audience(store, "ai_jobs") is audience


@pytest.mark.parametrize("audience", list(FeedbackAudience))
async def test_admin_selects_the_audience_with_packed_safe_action(
    audience: FeedbackAudience,
) -> None:
    store = _Store()
    runner = _runner(store)

    await callback_feedback_admin(
        _callback(f"fbadm:set_{audience.value}"),
        FeedbackAdminAction(action=f"set_{audience.value}"),
        runner,
        _bot_config(),
    )

    assert await get_feedback_audience(store, "ai_jobs") is audience


async def test_non_admin_cannot_change_the_audience() -> None:
    store = _Store()
    runner = _runner(store)

    await callback_feedback_admin(
        _callback("fbadm:set:all"),
        FeedbackAdminAction(action="set:all"),
        runner,
        _bot_config(admin=False),
    )

    assert await get_feedback_audience(store, "ai_jobs") is FeedbackAudience.OFF


async def test_an_unknown_audience_value_leaves_the_setting_alone() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ADMIN)
    runner = _runner(store)

    await callback_feedback_admin(
        _callback("fbadm:set:everyone"),
        FeedbackAdminAction(action="set:everyone"),
        runner,
        _bot_config(),
    )

    assert await get_feedback_audience(store, "ai_jobs") is FeedbackAudience.ADMIN


async def test_admin_only_mode_rejects_a_plain_reader() -> None:
    """The button is on every card, so the press itself must be authorized."""
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ADMIN)
    runner = _runner(store, job=_job())

    callback = _callback(f"fbk:{_REAL_PREFIX}", user_id=999)
    await callback_vacancy_feedback(
        callback, FeedbackAction(job_id=_REAL_PREFIX), runner, _bot_config()
    )

    assert await load_feedback(store, "ai_jobs") == []
    assert "только админы" in callback.answer.await_args[0][0]


async def test_admin_only_mode_accepts_the_admin() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ADMIN)
    runner = _runner(store, job=_job())

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}", user_id=123),
        FeedbackAction(job_id=_REAL_PREFIX),
        runner,
        _bot_config(),
    )

    assert len(await load_feedback(store, "ai_jobs")) == 1


async def test_admin_clear_empties_the_ledger() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())
    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}"), FeedbackAction(job_id=_REAL_PREFIX), runner, _bot_config()
    )

    await callback_feedback_admin(
        _callback("fbadm:clear"), FeedbackAdminAction(action="clear"), runner, _bot_config()
    )

    assert await load_feedback(store, "ai_jobs") == []


async def test_promotion_is_reported_not_applied() -> None:
    """Unreviewed negatives cost precision and recall, so promotion stays manual."""
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())
    for user_id in (1, 2):
        await callback_vacancy_feedback(
            _callback(f"fbk:{_REAL_PREFIX}", user_id=user_id),
            FeedbackAction(job_id=_REAL_PREFIX),
            runner,
            _bot_config(),
        )

    callback = _callback("fbadm:promote")
    await callback_feedback_admin(
        callback, FeedbackAdminAction(action="promote"), runner, _bot_config()
    )

    reply = callback.message.answer.await_args[0][0]
    assert "/negative_job" in reply
    # The profile itself must be untouched by the promote action.
    runner.save_and_activate_candidate_profile.assert_not_called()


async def test_cmd_feedback_requires_admin() -> None:
    store = _Store()
    runner = _runner(store)
    message = MagicMock()
    message.from_user = SimpleNamespace(id=123)
    message.answer = AsyncMock()

    await cmd_feedback(message, runner, _bot_config(admin=False))

    answered = message.answer.await_args[0][0] if message.answer.await_args else ""
    assert "Всего отметок" not in answered


def test_summary_marks_only_entries_that_reached_the_threshold() -> None:
    from job_ftch.application.vacancy_feedback import build_feedback

    records = [
        build_feedback(tenant_id="ai_jobs", job_id="a", user_id="1", title="Twice flagged"),
        build_feedback(tenant_id="ai_jobs", job_id="a", user_id="2", title="Twice flagged"),
        build_feedback(tenant_id="ai_jobs", job_id="b", user_id="1", title="Once flagged"),
    ]
    text = render_summary(
        summarize_feedback("ai_jobs", records), audience=FeedbackAudience.ALL, threshold=2
    )

    assert "2× Twice flagged ✅" in text
    assert "1× Once flagged" in text
    assert "1× Once flagged ✅" not in text


def test_summary_of_an_empty_ledger_says_so() -> None:
    text = render_summary(summarize_feedback("ai_jobs", []), audience=FeedbackAudience.OFF)

    assert "выключена" in text
    assert "Отметок пока нет." in text


async def test_prefix_is_expanded_through_the_publish_ledger() -> None:
    """Only a prefix fits in the button, so enrichment must recover the full id."""
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}"),
        FeedbackAction(job_id=_REAL_PREFIX),
        runner,
        _bot_config(),
    )

    runner.get_job.assert_awaited_once()
    assert runner.get_job.await_args[0][0] == _REAL_ID, "full id, not the prefix"
    assert (await load_feedback(store, "ai_jobs"))[0].title == "ML developer in geolocation team"


async def test_a_prefix_absent_from_the_ledger_still_records_the_verdict() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)
    runner = _runner(store, job=_job())
    store.values["bot_publish:sent_ids"] = json.dumps(["f" * 64])

    await callback_vacancy_feedback(
        _callback(f"fbk:{_REAL_PREFIX}"),
        FeedbackAction(job_id=_REAL_PREFIX),
        runner,
        _bot_config(),
    )

    records = await load_feedback(store, "ai_jobs")
    assert len(records) == 1
    assert records[0].title == ""
    runner.get_job.assert_not_awaited()
