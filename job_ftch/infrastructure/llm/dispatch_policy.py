"""Bounded parsing of provider errors; no inference, credentials or retry loops."""

from __future__ import annotations

import contextlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


def duration_seconds(value: Any) -> float | None:
    if isinstance(value, dict):
        try:
            value = float(value.get("seconds", 0)) + float(value.get("nanos", 0)) / 1e9
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        match = re.fullmatch(
            r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?",
            str(value).strip(),
        )
        if not match or not any(match.groups()):
            return None
        number = sum(
            float(part or 0) * unit
            for part, unit in zip(match.groups(), (3600, 60, 1), strict=True)
        )
    return number if math.isfinite(number) and 0 <= number <= 366 * 86400 else None


def retry_time(value: Any, *, now: datetime, default_seconds: float) -> datetime:
    seconds = duration_seconds(value)
    if seconds is not None:
        return now + timedelta(seconds=seconds)
    try:
        parsed = parsedate_to_datetime(str(value))
        return max(now, parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
    except (ValueError, TypeError, OverflowError):
        return now + timedelta(seconds=default_seconds)


@dataclass(frozen=True)
class DispatchSignal:
    reason: str
    retry_at: datetime | None = None
    operator: bool = False


def classify_response(
    response: httpx.Response, *, now: datetime, default_seconds: float
) -> DispatchSignal:
    """Honor both structured reset metadata and Retry-After, including wrappers."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    deadlines: list[datetime] = []
    reasons: set[str] = set()
    if response.headers.get("retry-after") is not None:
        deadlines.append(
            retry_time(response.headers["retry-after"], now=now, default_seconds=default_seconds)
        )

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(value, list):
            for child in value[:100]:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"reason", "code", "status", "type"}:
                    reasons.add(str(child).casefold())
                if key in {"retryDelay", "quotaResetDelay", "reset_seconds", "resets_in_seconds"}:
                    seconds = duration_seconds(child)
                    if seconds is not None:
                        deadlines.append(now + timedelta(seconds=seconds))
                if key in {"quotaResetTimeStamp", "quotaResetTimestamp", "resets_at"}:
                    try:
                        parsed = (
                            datetime.fromtimestamp(float(child), UTC)
                            if isinstance(child, (int, float))
                            else datetime.fromisoformat(str(child).replace("Z", "+00:00"))
                        )
                        deadlines.append(
                            max(now, parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
                        )
                    except (ValueError, TypeError, OverflowError, OSError):
                        pass
                visit(child, depth + 1)
        elif isinstance(value, str) and value.startswith("{"):
            with contextlib.suppress(ValueError):
                visit(json.loads(value), depth + 1)

    visit(body)
    text = json.dumps(body).casefold()
    if response.status_code == 402:
        return DispatchSignal("billing_required", operator=True)
    if response.status_code in {401, 403}:
        reason = "verification_required" if "validation_required" in reasons else "auth"
        return DispatchSignal(reason, operator=True)
    if response.status_code not in {408, 429, 500, 502, 503, 504}:
        return DispatchSignal("success" if response.is_success else "request_rejected")
    if "model_capacity_exhausted" in reasons:
        reason = "model_capacity"
    elif (
        reasons & {"quota_exhausted", "insufficient_quota", "usage_limit_reached", "model_cooldown"}
        or "insufficient_quota" in text
    ):
        reason = "quota"
    elif response.status_code == 429:
        reason = "rate_limit"
    else:
        reason = "provider_unavailable"
    return DispatchSignal(reason, max(deadlines, default=now + timedelta(seconds=default_seconds)))
