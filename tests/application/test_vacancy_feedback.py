"""Reader feedback accumulates as evidence, never as an automatic profile change."""

import pytest

from job_ftch.application.vacancy_feedback import (
    _FEEDBACK_LIMIT,
    build_feedback,
    clear_feedback,
    get_feedback_audience,
    is_feedback_enabled,
    load_feedback,
    may_submit_feedback,
    promotable_texts,
    record_feedback,
    set_feedback_audience,
    summarize_feedback,
)
from job_ftch.domain.feedback import FeedbackAudience, FeedbackVerdict


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_run_state(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.values[key] = value


def _feedback(job_id: str, user_id: str, **kwargs: object) -> object:
    return build_feedback(tenant_id="ai_jobs", job_id=job_id, user_id=user_id, **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_feedback_is_opt_in() -> None:
    store = _Store()
    assert await get_feedback_audience(store, "ai_jobs") is FeedbackAudience.OFF
    assert await is_feedback_enabled(store, "ai_jobs") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", list(FeedbackAudience))
async def test_audience_roundtrips(audience: FeedbackAudience) -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", audience)

    assert await get_feedback_audience(store, "ai_jobs") is audience
    assert await is_feedback_enabled(store, "ai_jobs") is audience.collects


@pytest.mark.asyncio
async def test_admin_only_still_puts_the_button_on_the_card() -> None:
    """The card is identical for everyone; permission is checked on the press."""
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ADMIN)

    assert await is_feedback_enabled(store, "ai_jobs") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", FeedbackAudience.ALL), ("0", FeedbackAudience.OFF)],
)
async def test_legacy_boolean_values_are_still_understood(
    raw: str, expected: FeedbackAudience
) -> None:
    """A channel that opted in before the three settings must keep its button."""
    store = _Store()
    store.values["bot_feedback:ai_jobs:enabled"] = raw

    assert await get_feedback_audience(store, "ai_jobs") is expected


@pytest.mark.asyncio
async def test_an_unknown_stored_value_falls_back_to_off() -> None:
    store = _Store()
    store.values["bot_feedback:ai_jobs:enabled"] = "everyone"

    assert await get_feedback_audience(store, "ai_jobs") is FeedbackAudience.OFF


@pytest.mark.parametrize(
    ("audience", "is_admin", "allowed"),
    [
        (FeedbackAudience.OFF, True, False),
        (FeedbackAudience.OFF, False, False),
        (FeedbackAudience.ADMIN, True, True),
        (FeedbackAudience.ADMIN, False, False),
        (FeedbackAudience.ALL, True, True),
        (FeedbackAudience.ALL, False, True),
    ],
)
def test_who_may_submit(audience: FeedbackAudience, is_admin: bool, allowed: bool) -> None:
    assert may_submit_feedback(audience, is_admin=is_admin) is allowed


@pytest.mark.asyncio
async def test_audience_is_per_tenant() -> None:
    store = _Store()
    await set_feedback_audience(store, "ai_jobs", FeedbackAudience.ALL)

    assert await get_feedback_audience(store, "other_tenant") is FeedbackAudience.OFF


@pytest.mark.asyncio
async def test_a_verdict_is_stored_and_reloaded() -> None:
    store = _Store()
    stored, _ = await record_feedback(
        store, _feedback("job-1", "42", title="ML developer", source_name="yandex")
    )

    assert stored is True
    records = await load_feedback(store, "ai_jobs")
    assert len(records) == 1
    assert records[0].job_id == "job-1"
    assert records[0].verdict is FeedbackVerdict.OFF_PROFILE


@pytest.mark.asyncio
async def test_the_same_reader_cannot_vote_twice_on_one_vacancy() -> None:
    """A double tap on a channel card is the same opinion, not a second vote."""
    store = _Store()
    await record_feedback(store, _feedback("job-1", "42"))
    stored, records = await record_feedback(store, _feedback("job-1", "42"))

    assert stored is False
    assert len(records) == 1


@pytest.mark.asyncio
async def test_different_readers_each_count() -> None:
    store = _Store()
    await record_feedback(store, _feedback("job-1", "42"))
    await record_feedback(store, _feedback("job-1", "43"))

    summary = summarize_feedback("ai_jobs", await load_feedback(store, "ai_jobs"))
    assert summary.total == 2
    assert summary.distinct_jobs == 1
    assert summary.top_jobs[0].votes == 2


@pytest.mark.asyncio
async def test_ledger_is_bounded() -> None:
    store = _Store()
    for index in range(_FEEDBACK_LIMIT + 25):
        await record_feedback(store, _feedback(f"job-{index}", "42"))

    assert len(await load_feedback(store, "ai_jobs")) == _FEEDBACK_LIMIT


@pytest.mark.asyncio
async def test_unreadable_ledger_does_not_raise() -> None:
    store = _Store()
    store.values["bot_feedback:ai_jobs:records"] = "{not json"

    assert await load_feedback(store, "ai_jobs") == []


@pytest.mark.asyncio
async def test_one_corrupt_row_does_not_void_the_ledger() -> None:
    store = _Store()
    await record_feedback(store, _feedback("job-1", "42"))
    raw = store.values["bot_feedback:ai_jobs:records"]
    store.values["bot_feedback:ai_jobs:records"] = raw.replace("[", '[{"bad": 1},', 1)

    assert len(await load_feedback(store, "ai_jobs")) == 1


@pytest.mark.asyncio
async def test_clear_removes_records_and_reports_the_count() -> None:
    store = _Store()
    await record_feedback(store, _feedback("job-1", "42"))
    await record_feedback(store, _feedback("job-2", "42"))

    assert await clear_feedback(store, "ai_jobs") == 2
    assert await load_feedback(store, "ai_jobs") == []


def test_summary_aggregates_by_source() -> None:
    records = [
        _feedback("job-1", "42", source_name="yandex"),
        _feedback("job-2", "43", source_name="yandex"),
        _feedback("job-3", "44", source_name="tbank"),
    ]
    summary = summarize_feedback("ai_jobs", records)  # type: ignore[arg-type]

    assert summary.by_source == {"yandex": 2, "tbank": 1}
    assert list(summary.by_source)[0] == "yandex", "busiest source leads"


def test_summary_of_nothing_is_empty() -> None:
    summary = summarize_feedback("ai_jobs", [])

    assert summary.is_empty
    assert summary.top_jobs == ()


def test_promotion_requires_agreement_from_distinct_readers() -> None:
    """One press is an opinion; the threshold is what makes it a signal."""
    text = "Generic ML role on a search product. " * 3
    records = [
        _feedback("job-1", "42", excerpt=text),
        _feedback("job-1", "43", excerpt=text),
        _feedback("job-2", "42", excerpt=text),
    ]
    summary = summarize_feedback("ai_jobs", records)  # type: ignore[arg-type]

    promoted = promotable_texts(summary, threshold=2)

    assert promoted == (text,), "only the twice-flagged vacancy is promotable"


def test_promotion_skips_texts_too_short_to_anchor() -> None:
    records = [_feedback("job-1", "42", excerpt="tiny"), _feedback("job-1", "43", excerpt="tiny")]
    summary = summarize_feedback("ai_jobs", records)  # type: ignore[arg-type]

    assert promotable_texts(summary, threshold=2) == ()


def test_promotion_is_empty_below_threshold() -> None:
    text = "Generic ML role on a search product. " * 3
    summary = summarize_feedback("ai_jobs", [_feedback("job-1", "42", excerpt=text)])  # type: ignore[arg-type]

    assert promotable_texts(summary, threshold=2) == ()


@pytest.mark.asyncio
async def test_ledgers_are_isolated_per_tenant() -> None:
    store = _Store()
    await record_feedback(store, _feedback("job-1", "42"))
    await record_feedback(store, build_feedback(tenant_id="other", job_id="job-1", user_id="42"))

    assert len(await load_feedback(store, "ai_jobs")) == 1
    assert len(await load_feedback(store, "other")) == 1
