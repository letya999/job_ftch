"""Rolling-window source quality labels from recent pipeline runs.

A source is:

- ``reliable`` when it is present in most of the last 20 pipeline runs and
  rarely fails (no WAF/deadline/protected/parser crash);
- ``rich`` when at least every second attempted run yields vacancies;
- ``high_relevance`` when at least every second attempted run also emits an
  accepted candidate (the relevance funnel, not raw yield).

Keyword-fanout clones (``*_kwN``) collapse onto the parent source so HireHi /
GeekJob search URLs do not look like 12 independent boards.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from job_ftch.application.pipeline import RunSummary

QUALITY_WINDOW_RUNS = 20
_KW_SUFFIX = re.compile(r"_kw\d+$")
_FAIL_STATUSES = frozenset(
    {
        "protected",
        "waf_challenge",
        "deadline_exceeded",
        "source_error",
        "transport_error",
        "upstream_error",
        "parser_error",
        "failed",
        "listing_discovery_failed",
        "detail_extraction_failed",
        "board_gone",
        "stale_url",
        "provider_tunnel_denied",
        "rate_limited",
    }
)


@dataclass(frozen=True, slots=True)
class SourceQualityStats:
    source_key: str
    window_runs: int
    attempted: int
    ok: int
    fail: int
    yield_hits: int
    relevant_hits: int
    yield_sum: int
    ok_rate: float
    yield_rate: float
    relevant_rate: float
    reliable: bool
    rich: bool
    high_relevance: bool

    def as_health_update(self) -> dict[str, object]:
        return {
            "quality_window_runs": self.window_runs,
            "quality_ok_rate": self.ok_rate,
            "quality_yield_rate": self.yield_rate,
            "quality_relevant_rate": self.relevant_rate,
            "quality_reliable": self.reliable,
            "quality_rich": self.rich,
            "quality_high_relevance": self.high_relevance,
        }

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def canonical_source_key(source_id: str | None, source_name: str | None = None) -> str:
    """Collapse ``career_site:foo_kw3`` / ``foo_kw3`` onto ``foo``."""
    raw = (source_id or "").strip() or (source_name or "").strip()
    if not raw:
        return "unknown"
    _, sep, name = raw.partition(":")
    token = name if sep else raw
    return _KW_SUFFIX.sub("", token)


def is_pipeline_run(summary: RunSummary) -> bool:
    """Drop 1-source probes and empty lock-skip summaries from the window."""
    if getattr(summary, "skipped_already_active", False):
        return False
    fetched = int(getattr(summary, "fetched", 0) or 0)
    outcomes = getattr(summary, "source_outcomes", None) or []
    return fetched >= 10 and len(outcomes) >= 2


def classify_source_quality(
    summaries: Sequence[RunSummary],
    *,
    window: int = QUALITY_WINDOW_RUNS,
) -> dict[str, SourceQualityStats]:
    """Label sources from the newest ``window`` real pipeline runs."""
    runs = [item for item in summaries if is_pipeline_run(item)][: max(window, 0)]
    window_runs = len(runs)
    if window_runs == 0:
        return {}

    attempted: dict[str, int] = {}
    ok: dict[str, int] = {}
    fail: dict[str, int] = {}
    yield_hits: dict[str, int] = {}
    relevant_hits: dict[str, int] = {}
    yield_sum: dict[str, int] = {}

    for summary in runs:
        per_run_yield: dict[str, int] = {}
        per_run_ok: dict[str, bool] = {}
        per_run_fail: dict[str, bool] = {}
        for outcome in getattr(summary, "source_outcomes", None) or []:
            if not isinstance(outcome, dict):
                continue
            key = canonical_source_key(
                str(outcome.get("source_id") or ""),
                str(outcome.get("source_name") or ""),
            )
            if key == "unknown":
                continue
            yielded = int(outcome.get("yielded") or 0)
            per_run_yield[key] = per_run_yield.get(key, 0) + yielded
            status = str(outcome.get("status") or "unknown")
            if status in _FAIL_STATUSES:
                per_run_fail[key] = True
            else:
                per_run_ok[key] = True
        emitted_by_key = _emitted_by_key(getattr(summary, "by_source_id", None) or {})
        for key in set(per_run_yield) | set(emitted_by_key):
            attempted[key] = attempted.get(key, 0) + 1
            if per_run_fail.get(key) and not per_run_ok.get(key):
                fail[key] = fail.get(key, 0) + 1
            else:
                ok[key] = ok.get(key, 0) + 1
            run_yield = per_run_yield.get(key, 0)
            yield_sum[key] = yield_sum.get(key, 0) + run_yield
            if run_yield > 0:
                yield_hits[key] = yield_hits.get(key, 0) + 1
            if emitted_by_key.get(key, 0) > 0:
                relevant_hits[key] = relevant_hits.get(key, 0) + 1

    out: dict[str, SourceQualityStats] = {}
    min_reliable_attempts = max(8, int(0.8 * window_runs))
    for key, att in attempted.items():
        ok_n = ok.get(key, 0)
        fail_n = fail.get(key, 0)
        y_hits = yield_hits.get(key, 0)
        r_hits = relevant_hits.get(key, 0)
        ok_rate = ok_n / att
        fail_rate = fail_n / att
        y_rate = y_hits / att
        r_rate = r_hits / att
        out[key] = SourceQualityStats(
            source_key=key,
            window_runs=window_runs,
            attempted=att,
            ok=ok_n,
            fail=fail_n,
            yield_hits=y_hits,
            relevant_hits=r_hits,
            yield_sum=yield_sum.get(key, 0),
            ok_rate=round(ok_rate, 4),
            yield_rate=round(y_rate, 4),
            relevant_rate=round(r_rate, 4),
            reliable=att >= min_reliable_attempts and fail_rate <= 0.15 and ok_rate >= 0.8,
            rich=att >= 4 and y_rate >= 0.5,
            high_relevance=att >= 4 and r_rate >= 0.5,
        )
    return out


def _emitted_by_key(by_source_id: Mapping[str, object]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for source_id, stats in by_source_id.items():
        key = canonical_source_key(str(source_id))
        totals[key] = totals.get(key, 0) + int(getattr(stats, "emitted", 0) or 0)
    return totals


def quality_payload(stats: Iterable[SourceQualityStats]) -> dict[str, object]:
    rows = [item.as_dict() for item in stats]
    return {
        "window_runs": next((item.window_runs for item in stats), 0),
        "reliable": [item.source_key for item in stats if item.reliable],
        "rich": [item.source_key for item in stats if item.rich],
        "high_relevance": [item.source_key for item in stats if item.high_relevance],
        "watch": [
            item.source_key
            for item in stats
            if item.high_relevance or (item.reliable and item.rich)
        ],
        "sources": rows,
    }
