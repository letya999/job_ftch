"""LLM availability preflight and durable quota backoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from job_ftch.application.logging import sanitize_string

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store


DEFAULT_QUOTA_RETRY_DELAY_SECONDS = 30 * 60
DEFAULT_QUOTA_MAX_RETRIES = 2
DEFAULT_SCHEDULE_INTERVAL_SECONDS = 12 * 60 * 60

_RETRY_COUNT_KEY = "bot_scheduler:llm_quota_retry_count"
_BLOCKED_UNTIL_KEY = "bot_scheduler:llm_quota_blocked_until"
_LAST_ERROR_KEY = "bot_scheduler:llm_preflight_last_error"
_STATUS_KEY = "bot_scheduler:llm_preflight_status"
_NEXT_DUE_KEY = "bot_scheduler:next_due_at"


class LLMQuotaExhaustedError(RuntimeError):
    """The configured LLM provider has no usable quota."""


@dataclass(frozen=True, slots=True)
class LLMPreflightResult:
    available: bool
    quota_exhausted: bool = False
    error: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class LLMQuotaDecision:
    allowed: bool
    status: str
    message: str = ""
    retry_at: datetime | None = None
    quota_exhausted: bool = False
    retry_number: int = 0


def is_quota_exhausted_error(error: BaseException) -> bool:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    body = getattr(error, "body", None)
    text = " ".join(
        str(value) for value in (error, getattr(error, "code", ""), body) if value is not None
    ).casefold()
    explicit_quota = any(
        marker in text
        for marker in (
            "insufficient_quota",
            "insufficient quota",
            "no credits remaining",
            "billing_hard_limit",
            "credit balance exhausted",
        )
    )
    return explicit_quota or (status_code == 402 and "quota" in text)


def safe_llm_error(error: BaseException) -> str:
    return sanitize_string(str(error) or type(error).__name__).replace("\n", " ")[:240]


def llm_error_reason(error: BaseException) -> str:
    """Return a stable low-cardinality reason for operator telemetry."""
    status_code = getattr(error, "status_code", None)
    text = str(error).casefold()
    if is_quota_exhausted_error(error):
        return "quota"
    if status_code in {401, 403} or any(
        marker in text for marker in ("api key", "unauthorized", "forbidden")
    ):
        return "auth"
    if status_code == 404 or ("model" in text and "not found" in text):
        return "model"
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if isinstance(error, TimeoutError) or "timeout" in text:
        return "timeout"
    if any(marker in text for marker in ("connection", "dns", "network", "transport")):
        return "transport"
    return "unknown"


async def run_llm_preflight(provider: object) -> LLMPreflightResult:
    """Call an optional provider preflight without coupling application to an adapter."""
    check = getattr(provider, "preflight", None)
    if not callable(check):
        return LLMPreflightResult(available=True)
    try:
        result = await check()
    except Exception as exc:  # noqa: BLE001 - provider boundary
        return LLMPreflightResult(
            available=False,
            quota_exhausted=is_quota_exhausted_error(exc),
            error=safe_llm_error(exc),
        )
    if isinstance(result, LLMPreflightResult):
        return result
    if isinstance(result, dict):
        return LLMPreflightResult(
            available=bool(result.get("available")),
            quota_exhausted=bool(result.get("quota_exhausted")),
            error=str(result.get("error") or ""),
            model=str(result.get("model") or ""),
        )
    return LLMPreflightResult(available=bool(result))


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_count(raw: object) -> int:
    if not isinstance(raw, (int, str)):
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def _save_state(
    store: Store,
    *,
    retry_count: int,
    blocked_until: datetime,
    status: str,
    error: str,
) -> None:
    await store.set_run_state(_RETRY_COUNT_KEY, str(retry_count))
    await store.set_run_state(_BLOCKED_UNTIL_KEY, blocked_until.isoformat())
    await store.set_run_state(_STATUS_KEY, status)
    await store.set_run_state(_LAST_ERROR_KEY, error)
    await store.set_run_state(_NEXT_DUE_KEY, blocked_until.isoformat())


async def _clear_state(store: Store) -> None:
    await store.set_run_state(_RETRY_COUNT_KEY, "0")
    await store.set_run_state(_BLOCKED_UNTIL_KEY, "")
    await store.set_run_state(_STATUS_KEY, "ready")
    await store.set_run_state(_LAST_ERROR_KEY, "")


def _quota_message(
    *,
    retry_at: datetime,
    retry_number: int,
    max_retries: int,
    disabled: bool,
    run_started: bool,
) -> str:
    prefix = "текущий ingestion остановлен" if run_started else "ingestion не запускался"
    if disabled:
        return (
            f"🤖 Квота LLM исчерпана. {prefix}. "
            f"Две повторные проверки исчерпаны; до следующего планового окна "
            f"({retry_at.astimezone(UTC).strftime('%d.%m %H:%M UTC')}) "
            "к провайдеру не обращаемся."
        )
    return (
        f"🤖 Квота LLM исчерпана. {prefix}. "
        f"Повторная проверка {retry_number}/{max_retries} "
        f"запланирована на {retry_at.astimezone(UTC).strftime('%d.%m %H:%M UTC')}."
    )


async def _record_quota_failure(
    store: Store,
    *,
    normal_interval_seconds: int,
    retry_delay_seconds: int,
    max_retries: int,
    now: datetime,
    run_started: bool,
) -> LLMQuotaDecision:
    current = _parse_count(await store.get_run_state(_RETRY_COUNT_KEY))
    blocked_until = _parse_timestamp(await store.get_run_state(_BLOCKED_UNTIL_KEY))
    if blocked_until is not None and blocked_until > now:
        return LLMQuotaDecision(
            allowed=False,
            status="retry_wait",
            message="Квота LLM уже на паузе до следующей проверки.",
            retry_at=blocked_until,
            quota_exhausted=True,
            retry_number=current,
        )
    retry_number = current + 1
    disabled = retry_number > max_retries
    retry_at = now + timedelta(seconds=normal_interval_seconds if disabled else retry_delay_seconds)
    persisted_count = max_retries if disabled else retry_number
    message = _quota_message(
        retry_at=retry_at,
        retry_number=min(retry_number, max_retries),
        max_retries=max_retries,
        disabled=disabled,
        run_started=run_started,
    )
    await _save_state(
        store,
        retry_count=persisted_count,
        blocked_until=retry_at,
        status="disabled_until_schedule" if disabled else "retry_scheduled",
        error="llm_quota_exhausted",
    )
    return LLMQuotaDecision(
        allowed=False,
        status="disabled_until_schedule" if disabled else "retry_scheduled",
        message=message,
        retry_at=retry_at,
        quota_exhausted=True,
        retry_number=min(retry_number, max_retries),
    )


async def check_llm_before_run(
    store: Store,
    provider: object,
    *,
    normal_interval_seconds: int = DEFAULT_SCHEDULE_INTERVAL_SECONDS,
    retry_delay_seconds: int = DEFAULT_QUOTA_RETRY_DELAY_SECONDS,
    max_retries: int = DEFAULT_QUOTA_MAX_RETRIES,
    now: datetime | None = None,
    preflight_result: LLMPreflightResult | None = None,
) -> LLMQuotaDecision:
    """Check the provider and schedule durable, bounded retries before any fetch."""
    now = now or datetime.now(UTC)
    normal_interval_seconds = max(1, int(normal_interval_seconds))
    retry_delay_seconds = max(1, int(retry_delay_seconds))
    max_retries = max(0, int(max_retries))
    blocked_until = _parse_timestamp(await store.get_run_state(_BLOCKED_UNTIL_KEY))
    retry_count = _parse_count(await store.get_run_state(_RETRY_COUNT_KEY))
    if blocked_until is not None and blocked_until > now:
        return LLMQuotaDecision(
            allowed=False,
            status="retry_wait",
            message="Квота LLM уже на паузе до следующей проверки.",
            retry_at=blocked_until,
            quota_exhausted=retry_count > 0,
            retry_number=retry_count,
        )

    result = preflight_result or await run_llm_preflight(provider)
    if result.available:
        if retry_count or blocked_until is not None:
            await _clear_state(store)
        return LLMQuotaDecision(allowed=True, status="ready")
    if result.quota_exhausted:
        return await _record_quota_failure(
            store,
            normal_interval_seconds=normal_interval_seconds,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
            now=now,
            run_started=False,
        )

    retry_at = now + timedelta(seconds=normal_interval_seconds)
    error = result.error or "llm_unavailable"
    await _save_state(
        store,
        retry_count=0,
        blocked_until=retry_at,
        status="unavailable_until_schedule",
        error=error,
    )
    return LLMQuotaDecision(
        allowed=False,
        status="unavailable_until_schedule",
        message=(
            f"🤖 LLM недоступна; ingestion не запускался. "
            f"Следующая проверка: {retry_at.astimezone(UTC).strftime('%d.%m %H:%M UTC')}."
        ),
        retry_at=retry_at,
    )


async def note_llm_quota_failure(
    store: Store,
    *,
    normal_interval_seconds: int,
    retry_delay_seconds: int = DEFAULT_QUOTA_RETRY_DELAY_SECONDS,
    max_retries: int = DEFAULT_QUOTA_MAX_RETRIES,
    now: datetime | None = None,
) -> LLMQuotaDecision:
    """Persist a quota failure discovered after a run has already started."""
    return await _record_quota_failure(
        store,
        normal_interval_seconds=max(1, int(normal_interval_seconds)),
        retry_delay_seconds=max(1, int(retry_delay_seconds)),
        max_retries=max(0, int(max_retries)),
        now=now or datetime.now(UTC),
        run_started=True,
    )
