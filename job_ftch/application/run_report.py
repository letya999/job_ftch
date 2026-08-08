"""Reusable run summary reporting for runtime adapters.

Adapters should not re-classify pipeline counters on their own.  This module
keeps the operational buckets close to the pipeline contracts so Telegram, MCP
or API surfaces can render the same run signal consistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


_SEEN_DROP_STAGES = frozenset({"dedup", "SnapshotFilterNode"})
_SEEN_DROP_REASONS = frozenset(
    {
        "already_processed",
        "already_seen",
        "duplicate_content",
        "duplicate_near_match",
        "duplicate_url",
    }
)
_NON_VACANCY_DROP_STAGES = frozenset(
    {"sanitize", "garbage", "post_type", "raw_jobness", "jobness", "hard_constraints"}
)
_NON_VACANCY_DROP_REASONS = frozenset(
    {
        "telegram_low_signal",
        "job_out_of_scope",
        "irrelevant_content",
        "non_job",
        "not_a_job",
    }
)
_LOW_RELEVANCE_DROP_REASONS = frozenset(
    {
        "low_relevance",
        "low_relevance_prefilter",
        "semantic_prefilter_low_relevance",
    }
)


@dataclass(frozen=True, slots=True)
class DropBucketStats:
    already_seen: int = 0
    non_vacancy: int = 0
    low_relevance: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return self.already_seen + self.non_vacancy + self.low_relevance + self.other


@dataclass(frozen=True, slots=True)
class RuntimeRunReport:
    duration_seconds: int
    fetched: int
    extracted: int
    duplicates: int
    dropped: int
    emitted: int
    failed: int
    drop_buckets: DropBucketStats
    notable_drop_reasons: dict[str, int]
    source_failures: list[dict[str, str]]
    llm_cost_usd: float
    llm_usage_requests: int
    llm_cost_is_complete: bool

    @property
    def seen_dominates_drops(self) -> bool:
        if self.dropped <= 0:
            return False
        return self.drop_buckets.already_seen >= int(self.dropped * 0.8)


def split_drop_buckets(drop_reasons: Mapping[str, int]) -> DropBucketStats:
    already_seen = 0
    non_vacancy = 0
    low_relevance = 0
    other = 0
    for reason, raw_count in drop_reasons.items():
        count = int(raw_count or 0)
        if count <= 0:
            continue
        stage = reason.partition(":")[2] if reason.startswith("node_returned_none") else ""
        if reason in _SEEN_DROP_REASONS or stage in _SEEN_DROP_STAGES:
            already_seen += count
        elif reason in _NON_VACANCY_DROP_REASONS or stage in _NON_VACANCY_DROP_STAGES:
            non_vacancy += count
        elif reason in _LOW_RELEVANCE_DROP_REASONS:
            low_relevance += count
        else:
            other += count
    return DropBucketStats(
        already_seen=already_seen,
        non_vacancy=non_vacancy,
        low_relevance=low_relevance,
        other=other,
    )


def build_runtime_run_report(summary: object, *, duration_seconds: int) -> RuntimeRunReport:
    drop_reasons: dict[str, int] = getattr(summary, "drop_reasons", {}) or {}
    notable_drop_reasons = {
        reason: count
        for reason, count in drop_reasons.items()
        if count > 0
        and reason
        in {
            "telegram_low_signal",
            "job_out_of_scope",
            "irrelevant_content",
            "duplicate_content",
            "low_relevance_prefilter",
        }
    }
    return RuntimeRunReport(
        duration_seconds=max(0, int(duration_seconds)),
        fetched=int(getattr(summary, "fetched", 0) or 0),
        extracted=int(getattr(summary, "extracted", 0) or 0),
        duplicates=int(getattr(summary, "duplicates", 0) or 0),
        dropped=int(getattr(summary, "dropped", 0) or 0),
        emitted=int(getattr(summary, "emitted", 0) or 0),
        failed=int(getattr(summary, "failed", 0) or 0),
        drop_buckets=split_drop_buckets(drop_reasons),
        notable_drop_reasons=notable_drop_reasons,
        source_failures=list(getattr(summary, "source_failures", []) or []),
        llm_cost_usd=float(getattr(summary, "llm_cost_usd", 0.0) or 0.0),
        llm_usage_requests=int(getattr(summary, "llm_usage_requests", 0) or 0),
        llm_cost_is_complete=bool(getattr(summary, "llm_cost_is_complete", True)),
    )


def render_runtime_run_report_text(report: RuntimeRunReport) -> str:
    funnel_parts = [
        f"📥 Получено:         {report.fetched}",
        f"👁 Уже видели:       {report.drop_buckets.already_seen}",
        f"🚫 Не-вакансии:      {report.drop_buckets.non_vacancy}",
    ]
    if report.drop_buckets.low_relevance:
        funnel_parts.append(f"📉 Низкая релевантность: {report.drop_buckets.low_relevance}")
    if report.drop_buckets.other:
        funnel_parts.append(f"➖ Прочие дропы:     {report.drop_buckets.other}")
    funnel_parts += [
        f"🔄 Дубликаты:        {report.duplicates}",
        f"⚙️ Обработано:       {report.extracted}",
        f"⭐ Найдено вакансий: {report.emitted}",
    ]
    text = "\n".join(funnel_parts)
    if report.notable_drop_reasons:
        reasons_str = ", ".join(
            f"{reason.replace('_', ' ')}: {count}"
            for reason, count in report.notable_drop_reasons.items()
        )
        text += f"\n<i>Причины дропа: {reasons_str}</i>"
    if report.source_failures:
        problem_lines = [
            f"{item.get('source_name') or item.get('source_id')}: {item.get('error')}"
            for item in report.source_failures
        ]
        text += "\n<i>Проблемные источники: " + "; ".join(problem_lines) + "</i>"
    return text


def render_runtime_run_footer(report: RuntimeRunReport) -> str:
    footer = f"⏱ {report.duration_seconds}с"
    if report.llm_usage_requests and report.llm_cost_is_complete:
        footer += f"  •  🤖 {report.llm_usage_requests} LLM  •  ${report.llm_cost_usd:.4f}"
    elif report.llm_usage_requests:
        footer += f"  •  🤖 {report.llm_usage_requests} LLM  •  цена: нет тарифа модели"
    if report.failed > 0:
        footer += f"  •  ошибок: {report.failed}"
    return footer
