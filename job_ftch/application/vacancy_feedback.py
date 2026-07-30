"""Accumulate, aggregate and promote reader feedback on published vacancies.

Stored in run state rather than a dedicated table so every backend supports it without a
migration, following the publish-ledger pattern.

Feedback deliberately does not feed the relevance judge on its own. Measured on
2026-07-24, injecting negative shots without review lowered accepted precision and dropped
recall, because off-profile vacancies sit close to the profile's own target roles in
embedding space. Feedback therefore accumulates as evidence and is promoted into profile
negatives only by an explicit action, once enough distinct readers agree.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog

from job_ftch.domain.feedback import (
    FeedbackAudience,
    FeedbackJobTally,
    FeedbackSummary,
    FeedbackVerdict,
    VacancyFeedback,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

logger = structlog.get_logger(__name__)

# Keep the ledger bounded: it lives in a single run-state value, and an unbounded list
# would grow into every read of that key.
_FEEDBACK_LIMIT = 1000
_DEFAULT_PROMOTION_THRESHOLD = 2
_TOP_JOBS = 10


class RunStateStore(Protocol):
    """Smallest store contract required by the feedback ledger."""

    async def get_run_state(self, key: str) -> str | None: ...

    async def set_run_state(self, key: str, value: str) -> None: ...


def _ledger_key(tenant_id: str) -> str:
    return f"bot_feedback:{tenant_id}:records"


def _audience_key(tenant_id: str) -> str:
    return f"bot_feedback:{tenant_id}:enabled"


async def _maybe_await(value: object) -> object:
    if hasattr(value, "__await__"):
        return await cast("Awaitable[Any]", value)
    return value


async def get_feedback_audience(store: RunStateStore, tenant_id: str) -> FeedbackAudience:
    """Who may flag a card. Opt-in: an unconfigured channel keeps its plain cards."""
    raw = await _maybe_await(store.get_run_state(_audience_key(tenant_id)))
    if not isinstance(raw, str):
        return FeedbackAudience.OFF
    # "1"/"0" are the values written before the audience had three settings; a channel
    # that already opted in must not silently lose its button on upgrade.
    if raw == "1":
        return FeedbackAudience.ALL
    if raw == "0":
        return FeedbackAudience.OFF
    try:
        return FeedbackAudience(raw)
    except ValueError:
        return FeedbackAudience.OFF


async def set_feedback_audience(
    store: RunStateStore, tenant_id: str, audience: FeedbackAudience
) -> None:
    await _maybe_await(store.set_run_state(_audience_key(tenant_id), audience.value))


async def is_feedback_enabled(store: RunStateStore, tenant_id: str) -> bool:
    """True when cards should carry the button, regardless of who may press it."""
    return (await get_feedback_audience(store, tenant_id)).collects


def may_submit_feedback(audience: FeedbackAudience, *, is_admin: bool) -> bool:
    """Authorize one press.

    Checked when the press arrives rather than when the card is built: the same published
    card is seen by everyone, so the button's presence cannot encode permission.
    """
    if audience is FeedbackAudience.OFF:
        return False
    if audience is FeedbackAudience.ADMIN:
        return is_admin
    return True


async def load_feedback(store: RunStateStore, tenant_id: str) -> list[VacancyFeedback]:
    raw = await _maybe_await(store.get_run_state(_ledger_key(tenant_id)))
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("feedback_ledger_unreadable", tenant_id=tenant_id)
        return []
    if not isinstance(rows, list):
        return []
    records: list[VacancyFeedback] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            records.append(VacancyFeedback.model_validate(row))
        except Exception:  # noqa: BLE001 - one bad row must not void the ledger
            continue
    return records


async def _persist(
    store: RunStateStore, tenant_id: str, records: Sequence[VacancyFeedback]
) -> list[VacancyFeedback]:
    bounded = list(records)[-_FEEDBACK_LIMIT:]
    payload = json.dumps([record.model_dump(mode="json") for record in bounded], ensure_ascii=False)
    await _maybe_await(store.set_run_state(_ledger_key(tenant_id), payload))
    return bounded


async def record_feedback(
    store: RunStateStore, feedback: VacancyFeedback
) -> tuple[bool, list[VacancyFeedback]]:
    """Append one verdict. Returns (stored, ledger).

    ``stored`` is False when this reader already flagged this vacancy, so a double tap on
    a channel card cannot inflate a job's vote count.
    """
    records = await load_feedback(store, feedback.tenant_id)
    if any(record.identity == feedback.identity for record in records):
        return False, records
    records.append(feedback)
    return True, await _persist(store, feedback.tenant_id, records)


async def clear_feedback(store: RunStateStore, tenant_id: str) -> int:
    records = await load_feedback(store, tenant_id)
    await _maybe_await(store.set_run_state(_ledger_key(tenant_id), "[]"))
    return len(records)


def summarize_feedback(tenant_id: str, records: Sequence[VacancyFeedback]) -> FeedbackSummary:
    """Aggregate by vacancy and by source so a systematic leak is visible."""
    by_source: dict[str, int] = {}
    by_job: dict[str, list[VacancyFeedback]] = {}
    for record in records:
        source = record.source_name or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
        by_job.setdefault(record.job_id, []).append(record)

    tallies = [
        FeedbackJobTally(
            job_id=job_id,
            title=group[0].title,
            url=group[0].url,
            source_name=group[0].source_name,
            excerpt=group[0].excerpt,
            votes=len(group),
        )
        for job_id, group in by_job.items()
    ]
    tallies.sort(key=lambda tally: (-tally.votes, tally.title))

    return FeedbackSummary(
        tenant_id=tenant_id,
        total=len(records),
        distinct_jobs=len(by_job),
        by_source=dict(sorted(by_source.items(), key=lambda item: (-item[1], item[0]))),
        top_jobs=tuple(tallies[:_TOP_JOBS]),
    )


def promotable_texts(
    summary: FeedbackSummary,
    *,
    threshold: int = _DEFAULT_PROMOTION_THRESHOLD,
    min_chars: int = 30,
) -> tuple[str, ...]:
    """Vacancy texts agreed off-profile by enough distinct readers.

    Returned for the caller to feed through the existing negative-example path. A single
    press is an opinion; the threshold is what makes it a signal. Texts shorter than the
    example minimum are skipped because they make weak anchors.
    """
    return tuple(
        tally.excerpt
        for tally in summary.top_jobs
        if tally.votes >= threshold and len(tally.excerpt) >= min_chars
    )


def build_feedback(
    *,
    tenant_id: str,
    job_id: str,
    user_id: str,
    title: str = "",
    url: str = "",
    source_name: str = "",
    excerpt: str = "",
) -> VacancyFeedback:
    return VacancyFeedback(
        tenant_id=tenant_id,
        job_id=job_id,
        user_id=user_id,
        verdict=FeedbackVerdict.OFF_PROFILE,
        title=title,
        url=url,
        source_name=source_name,
        excerpt=excerpt[:4000],
    )
