"""Async source -> nodes -> sink orchestration."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import uuid4

if TYPE_CHECKING:
    from job_ftch.application.contracts import (
        DeliveryTarget,
        SanitizingNode,
        Sink,
        Source,
        Stage,
        Store,
    )
    from job_ftch.nodes.snapshot_filter import SnapshotFilterNode

import structlog
from opentelemetry import trace

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.item_decision_trace import record_item_decision_trace
from job_ftch.application.logging import sanitize_string
from job_ftch.application.outbox import recover_pending_outbox
from job_ftch.application.rejections import RawItemRejected
from job_ftch.application.resolver import DeferredResolverQueue
from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.config import get_settings
from job_ftch.domain import (
    Job,
    JobExtractionStatus,
    JobRecord,
    MatchDecision,
    QuarantinedRawItem,
    RawItem,
    RawItemRejectionReason,
    RejectedItem,
    RejectedOutcome,
    ResolutionTask,
    processed_key_for_raw_item,
)
from job_ftch.domain.observation import ObservationLedgerEntry, content_hash_for_raw_item
from job_ftch.domain.outbox import OutboxRecord, OutboxState, delivery_idempotency_key
from job_ftch.nodes.sanitize import SanitizeNode

PipelineInput = TypeVar("PipelineInput")
PipelineOutput = TypeVar("PipelineOutput")

_NODE_DROP_REASON = "node_returned_none"


def _drop_reason(result: dict[str, Any]) -> str:
    """Name the node that dropped an item.

    Every silent node drop used to collapse into a single ``node_returned_none``
    bucket, so a run drained by dedup was indistinguishable from a run whose
    candidates were genuinely rejected as non-vacancies. The dropping stage is
    already carried on the worker result; keep the historical prefix so existing
    aggregations still match, and append the stage for diagnosis.
    """
    explicit = str(result.get("drop_reason") or "").strip()
    if explicit:
        return explicit
    stage = str(result.get("drop_stage") or "").strip()
    return f"{_NODE_DROP_REASON}:{stage}" if stage else _NODE_DROP_REASON


@dataclass(slots=True)
class StatsBase:
    fetched: int = 0
    sanitized: int = 0
    triaged: int = 0
    extracted: int = 0
    partial: int = 0
    review: int = 0
    duplicates: int = 0
    dropped: int = 0
    emitted: int = 0
    posted: int = 0
    rejected: int = 0
    quarantined: int = 0
    failed: int = 0
    deferred: int = 0
    new_groups_created: int = 0
    merged_into_group: int = 0
    monitored: int = 0  # URLs/items discovered by monitor
    rich_emitted: int = 0  # items from rich monitors (no scraper needed)
    scraped: int = 0  # items processed by scraper
    scrape_fallback_used: int = 0  # times fallback scraper was triggered
    source_partial: bool = False  # at least one monitor was truncated
    monitor_truncated: int = 0  # count of truncated monitor runs
    candidate_children_produced: int = 0
    candidate_children_processed: int = 0
    detail_pages_scraped: int = 0
    browser_navigations_attempted: int = 0
    llm_calls_used: int = 0
    source_deadline_reached: bool = False
    source_limited: bool = False
    drop_reasons: dict[str, int] = field(default_factory=dict)
    quarantine_reasons: dict[str, int] = field(default_factory=dict)

    # LLM observability (ADR-029, ADR-030)
    llm_relevance_calls: int = 0
    llm_relevance_cache_hits: int = 0
    llm_relevance_fallback: int = 0  # exceeded max_per_run
    llm_relevance_failures: int = 0
    llm_presentable_calls: int = 0
    llm_presentable_cache_hits: int = 0
    llm_presentable_fallback: int = 0  # exceeded max_per_run or template fallback
    llm_tokens_in: int = 0
    llm_cached_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_latency_ms: int = 0
    llm_cost_usd: float = 0.0
    llm_usage_requests: int = 0
    llm_cost_is_complete: bool = True
    llm_cost_pricing_version: str | None = None
    llm_cost_unknown_models: list[str] = field(default_factory=list)

    def record_drop(self, reason: str) -> None:
        self.dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1

    def record_deferred(self, reason: str = "evidence_missing") -> None:
        self.deferred += 1
        self.drop_reasons[f"deferred:{reason}"] = self.drop_reasons.get(f"deferred:{reason}", 0) + 1

    def record_quarantine(self, reason: str) -> None:
        self.quarantined += 1
        self.quarantine_reasons[reason] = self.quarantine_reasons.get(reason, 0) + 1

    def record_rejected(self) -> None:
        self.rejected += 1

    def record_duplicate(self) -> None:
        self.duplicates += 1

    def record_failure(self) -> None:
        self.failed += 1

    def record_group_created(self) -> None:
        self.new_groups_created += 1

    def record_merged_into_group(self) -> None:
        self.merged_into_group += 1


@dataclass(slots=True)
class SourceRunStats(StatsBase):
    pass


@dataclass(slots=True)
class RunSummary(StatsBase):
    by_source_kind: dict[str, SourceRunStats] = field(default_factory=dict)
    by_source_id: dict[str, SourceRunStats] = field(default_factory=dict)
    tenant_id: str | None = None
    applied_profile: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    scheduled_run_index: int = 0
    source_run_id: str | None = None
    source_failures: list[dict[str, str]] = field(default_factory=list)
    source_evictions: list[dict[str, str]] = field(default_factory=list)
    source_outcomes: list[dict[str, object]] = field(default_factory=list)
    graph_hash: str | None = None
    graph_node_metrics: dict[str, dict[str, object]] = field(default_factory=dict)
    # True when the run never executed because another run held the tenant lock.
    # Without this flag an empty summary is indistinguishable from a real run
    # that found nothing, and adapters report "nothing found" for work that
    # never happened.
    skipped_already_active: bool = False

    def finish(self) -> RunSummary:
        self.finished_at = datetime.now(UTC)
        return self

    def source_stats(self, source_kind: object | None) -> SourceRunStats:
        key = str(source_kind or "unknown")
        stats = self.by_source_kind.get(key)
        if stats is None:
            stats = SourceRunStats()
            self.by_source_kind[key] = stats
        return stats

    def source_identity_stats(
        self, source_kind: object | None, source_name: object | None
    ) -> SourceRunStats | None:
        normalized_name = str(source_name).strip() if source_name is not None else ""
        if not normalized_name:
            return None
        key = f"{source_kind or 'unknown'}:{normalized_name}"
        stats = self.by_source_id.get(key)
        if stats is None:
            stats = SourceRunStats()
            self.by_source_id[key] = stats
        return stats

    def record_source_drop(
        self, source_kind: object | None, reason: str, source_name: object | None = None
    ) -> None:
        self.record_drop(reason)
        self.source_stats(source_kind).record_drop(reason)
        stats = self.source_identity_stats(source_kind, source_name)
        if stats is not None:
            stats.record_drop(reason)

    def record_source_quarantine(
        self, source_kind: object | None, reason: str, source_name: object | None = None
    ) -> None:
        self.record_quarantine(reason)
        self.source_stats(source_kind).record_quarantine(reason)
        stats = self.source_identity_stats(source_kind, source_name)
        if stats is not None:
            stats.record_quarantine(reason)

    def record_source_group_created(
        self, source_kind: object | None, source_name: object | None = None
    ) -> None:
        self.record_group_created()
        self.source_stats(source_kind).record_group_created()
        stats = self.source_identity_stats(source_kind, source_name)
        if stats is not None:
            stats.record_group_created()

    def record_source_merged_into_group(
        self, source_kind: object | None, source_name: object | None = None
    ) -> None:
        self.record_merged_into_group()
        self.source_stats(source_kind).record_merged_into_group()
        stats = self.source_identity_stats(source_kind, source_name)
        if stats is not None:
            stats.record_merged_into_group()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class Pipeline[PipelineInput, PipelineOutput]:
    def __init__(
        self,
        source: Source[PipelineInput],
        sanitize_node: SanitizingNode[PipelineInput],
        nodes: Sequence[Stage[Any, Any]],
        sink: Sink[PipelineOutput] | Sequence[Sink[PipelineOutput]],
        store: Store,
        quarantine_sink: Sink[QuarantinedRawItem] | None = None,
        rejected_sink: Sink[RejectedItem] | None = None,
        snapshot_filter: SnapshotFilterNode | None = None,
        pipeline_item_concurrency: int = 1,
        source_run_id: str | None = None,
        delivery_targets: Sequence[DeliveryTarget[JobRecord]] = (),
    ) -> None:
        if not (
            isinstance(sanitize_node, SanitizeNode)
            or getattr(sanitize_node, "_is_sanitizer", False)
        ):
            msg = (
                "Pipeline.sanitize_node must be a SanitizeNode (or a custom "
                "class with `_is_sanitizer = True`). Got "
                f"{type(sanitize_node).__name__}."
            )
            raise TypeError(msg)
        self._source = source
        self._sanitize_node = sanitize_node
        self._nodes = list(nodes)
        self._sink = self._coerce_sink(sink)
        # Optional SnapshotFilterNode: when set, Pipeline.run() calls
        # save_and_purge() at the end so the run is persisted and the
        # 7-day TTL is applied. Per ADR-031 + ADR-036.
        self._snapshot_filter = snapshot_filter
        self._store = store
        self._quarantine_sink = quarantine_sink
        self._rejected_sink = rejected_sink
        self._item_concurrency = max(1, pipeline_item_concurrency)
        self._source_run_id = source_run_id
        self._delivery_targets = {target.target_id: target for target in delivery_targets}
        if len(self._delivery_targets) != len(delivery_targets):
            raise ValueError("Delivery target IDs must be unique")
        self._logger = structlog.get_logger("job_ftch.pipeline")
        self._tracer = trace.get_tracer("job_ftch.pipeline")

    async def run(self, max_items: int | None = None) -> RunSummary:
        settings = get_settings()
        summary = RunSummary()
        summary.source_run_id = self._source_run_id or uuid4().hex
        await self._recover_pending_outbox()
        await self._set_run_state("pipeline.started_at", summary.started_at.isoformat())
        await self._set_run_state("pipeline.status", "running")
        await self._set_run_state("pipeline.source_run_id", summary.source_run_id)
        await self._set_run_state("pipeline.item_concurrency", str(self._item_concurrency))
        run_interrupted = False
        with self._tracer.start_as_current_span("pipeline.run") as span:
            span.set_attribute("job_ftch.source_run_id", summary.source_run_id)
            run_interrupted = await self._run_concurrent(summary, max_items, settings)
            summary.finish()

            # Collect additional stats from nodes
            for node in self._nodes:
                if hasattr(node, "stats"):
                    node_stats = getattr(node, "stats", {})
                    for key, val in node_stats.items():
                        if hasattr(summary, key):
                            if isinstance(val, int):
                                setattr(summary, key, getattr(summary, key) + val)
                            else:
                                setattr(summary, key, val)

            # Collect additional stats from source if available (e.g. CareerSiteSource)
            if hasattr(self._source, "stats"):
                source_stats = getattr(self._source, "stats", {})
                for key, val in source_stats.items():
                    if hasattr(summary, key):
                        if isinstance(val, int):
                            setattr(summary, key, getattr(summary, key) + val)
                        else:
                            setattr(summary, key, val)

                # Also update by_source_kind if possible
                source_kind = getattr(self._source, "kind", "career_site")
                sk_stats = summary.source_stats(source_kind)
                source_name = getattr(self._source, "source_name", None)
                sid_stats = summary.source_identity_stats(source_kind, source_name)
                for key, val in source_stats.items():
                    if hasattr(sk_stats, key):
                        if isinstance(val, int):
                            setattr(sk_stats, key, getattr(sk_stats, key) + val)
                        else:
                            setattr(sk_stats, key, val)
                    if sid_stats is not None and hasattr(sid_stats, key):
                        if isinstance(val, int):
                            setattr(sid_stats, key, getattr(sid_stats, key) + val)
                        else:
                            setattr(sid_stats, key, val)

            for node in self._nodes:
                try:
                    await self._flush_if_supported(node)
                except Exception:
                    summary.record_failure()
                    run_interrupted = True
                    self._logger.exception(
                        "pipeline_node_flush_failed",
                        node=node.__class__.__name__,
                    )

            try:
                await self._flush_if_supported(self._sink)
            except Exception:
                summary.record_failure()
                run_interrupted = True
                self._logger.exception(
                    "pipeline_sink_flush_failed",
                    sink=self._sink.__class__.__name__,
                )
            if self._quarantine_sink is not None:
                try:
                    await self._flush_if_supported(self._quarantine_sink)
                except Exception:
                    summary.record_failure()
                    self._logger.exception(
                        "pipeline_secondary_sink_flush_failed",
                        sink="quarantine",
                    )
            if self._rejected_sink is not None:
                try:
                    await self._flush_if_supported(self._rejected_sink)
                except Exception:
                    summary.record_failure()
                    self._logger.exception(
                        "pipeline_secondary_sink_flush_failed",
                        sink="rejected",
                    )
            span.set_attribute("job_ftch.fetched", summary.fetched)
            span.set_attribute("job_ftch.sanitized", summary.sanitized)
            span.set_attribute("job_ftch.triaged", summary.triaged)
            span.set_attribute("job_ftch.extracted", summary.extracted)
            span.set_attribute("job_ftch.partial", summary.partial)
            span.set_attribute("job_ftch.review", summary.review)
            span.set_attribute("job_ftch.duplicates", summary.duplicates)
            span.set_attribute("job_ftch.dropped", summary.dropped)
            self._record_graph_metrics(summary)
            # RoutingSink applies the terminal lane after this orchestrator
            # returns.  At this point the count is only attempted sink emits,
            # not ACCEPT and not Telegram delivery.
            span.set_attribute("job_ftch.pipeline_sink_attempted", summary.emitted)
            span.set_attribute("job_ftch.rejected", summary.rejected)
            span.set_attribute("job_ftch.quarantined", summary.quarantined)
            span.set_attribute("job_ftch.failed", summary.failed)
        finished_at = summary.finished_at or datetime.now(UTC)
        await self._set_run_state("pipeline.finished_at", finished_at.isoformat())
        self._record_source_failures(summary)
        if self._snapshot_filter is not None:
            try:
                source_results = getattr(self._source, "source_results", None)
                completed_source_ids = (
                    frozenset(
                        result.source_id
                        for result in source_results.values()
                        if not result.failed and not result.partial and not result.limited
                    )
                    if isinstance(source_results, dict)
                    else None
                )
                await self._snapshot_filter.save_and_purge(
                    source_run_complete=not run_interrupted,
                    completed_source_ids=completed_source_ids,
                )
            except Exception as exc:  # noqa: BLE001
                summary.record_failure()
                run_interrupted = True
                self._logger.warning(
                    "snapshot_filter_save_failed",
                    run_id=getattr(self._snapshot_filter, "_run_id", None),
                    error=str(exc),
                )

        if run_interrupted:
            await self._set_run_state("pipeline.status", "failed")
        elif summary.failed > 0:
            await self._set_run_state("pipeline.status", "completed_with_failures")
        else:
            await self._set_run_state("pipeline.status", "completed")

        self._logger.info(
            "pipeline_core_complete",
            run_id=summary.source_run_id,
            graph_hash=summary.graph_hash,
            fetched=summary.fetched,
            extracted=summary.extracted,
            candidate_sink_attempted=summary.emitted,
            dropped=summary.dropped,
            failed=summary.failed,
            source_outcomes=summary.source_outcomes,
        )
        return summary

    def _record_source_failures(self, summary: RunSummary) -> None:
        source_results = getattr(self._source, "source_results", None)
        if not isinstance(source_results, dict):
            return
        for result in source_results.values():
            source_identity = summary.by_source_id.get(result.source_id)
            should_materialize_identity = result.failed or result.yielded == 0
            if source_identity is None and should_materialize_identity:
                source_identity = summary.source_identity_stats(
                    result.source_kind, result.source_name
                )
            summary.source_stats(result.source_kind)
            if source_identity is not None and source_identity.fetched == 0 and result.yielded > 0:
                source_identity.fetched = result.yielded
            if source_identity is not None and source_identity.fetched == 0 and not result.failed:
                source_identity.fetched = result.yielded
            source_kind_stats = summary.source_stats(result.source_kind)
            source_kind_stats.monitored += result.monitored
            source_kind_stats.rich_emitted += result.rich_emitted
            source_kind_stats.scraped += result.scraped
            source_kind_stats.scrape_fallback_used += result.scrape_fallback_used
            source_kind_stats.monitor_truncated += result.monitor_truncated
            source_kind_stats.source_partial = source_kind_stats.source_partial or result.partial
            if source_identity is not None:
                source_identity.monitored = result.monitored
                source_identity.rich_emitted = result.rich_emitted
                source_identity.scraped = result.scraped
                source_identity.scrape_fallback_used = result.scrape_fallback_used
                source_identity.monitor_truncated = result.monitor_truncated
                source_identity.source_partial = result.partial
            summary.monitored += result.monitored
            summary.rich_emitted += result.rich_emitted
            summary.scraped += result.scraped
            summary.scrape_fallback_used += result.scrape_fallback_used
            summary.monitor_truncated += result.monitor_truncated
            summary.source_partial = summary.source_partial or result.partial or result.failed
            summary.source_outcomes.append(
                {
                    "source_id": result.source_id,
                    "source_kind": result.source_kind,
                    "source_name": result.source_name,
                    "status": result.terminal_outcome
                    or ("failed" if result.failed else "partial" if result.partial else "ok"),
                    "completion_state": result.completion_state,
                    "yielded": result.yielded,
                    "monitored": result.monitored,
                    "scraped": result.scraped,
                    "freshness_filtered": result.freshness_filtered,
                    "freshness_undated_passed": result.freshness_undated_passed,
                    "parser_duplicates_suppressed": result.parser_duplicates_suppressed,
                    "zero_reason": result.zero_reason,
                    "error": result.error,
                }
            )
            if result.failed:
                source_kind_stats.record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
                summary.source_failures.append(
                    {
                        "source_id": result.source_id,
                        "source_kind": result.source_kind,
                        "source_name": result.source_name,
                        "error": result.error or "source_fetch_failed",
                    }
                )
            if result.evicted:
                summary.source_evictions.append(
                    {
                        "source_id": result.source_id,
                        "source_kind": result.source_kind,
                        "source_name": result.source_name,
                        "eviction_kind": result.eviction_kind or "soft_deadline",
                    }
                )

    def _blank_item_result(self, item: Any) -> dict[str, Any]:
        """Build the per-item result envelope shared by the worker and the
        main-loop duplicate-skip path, so both feed identical shapes to the
        finalizer."""
        item_id = getattr(item, "stable_id", None)
        processed_key = processed_key_for_raw_item(item) if isinstance(item, RawItem) else item_id
        return {
            "item": item,
            "item_id": item_id,
            "source_kind": getattr(item, "source_kind", None),
            "source_name": getattr(item, "source_name", None),
            "processed_key": processed_key,
            "outcome": None,
            "current": None,
            "exc": None,
            "drop_reason": None,
            "drop_details": None,
            "drop_stage": None,
            "failure_reason": "item_processing_failed",
            "failure_stage": self._sanitize_node.__class__.__name__,
            "passed_sanitize": False,
        }

    async def _process_item_worker(
        self,
        item: Any,
        source_run_id: str,
        settings: Any,
        work_budget: AsyncCallBudget | None = None,
    ) -> dict[str, Any]:
        """Process one item through sanitize+nodes. Returns a result dict for the finalizer.

        Never touches summary, sink, or mark_processed — those stay in the single-threaded
        finalizer so there are no concurrent-write races on shared state.
        """
        item_id = getattr(item, "stable_id", None)
        source_kind = getattr(item, "source_kind", None)
        source_name = getattr(item, "source_name", None)
        result = self._blank_item_result(item)
        processed_key = result["processed_key"]
        if await self._handle_pre_quarantined_item_worker(item, result):
            return result

        with self._tracer.start_as_current_span("pipeline.item") as item_span:
            item_span.set_attribute("job_ftch.item_id", str(item_id or ""))
            item_span.set_attribute("job_ftch.source_name", str(source_name or ""))
            item_span.set_attribute(
                "job_ftch.source_kind",
                str(getattr(source_kind, "value", source_kind) or ""),
            )
            try:
                # The per-run LLM budget is enforced by an atomic AsyncCallBudget
                # shared into the LLM node, which reserves a unit immediately before
                # each call (see builder.run_llm_budget). The old guard here counted
                # calls only after node.process() returned, so several concurrent
                # workers could all pass it at the limit and overshoot the cap.
                if isinstance(item, RawItem):
                    await self._record_observation(item, settings)
                if processed_key is not None and await self._store.has_processed(processed_key):
                    result["outcome"] = "already_processed"
                    item_span.set_attribute("job_ftch.result", "already_processed")
                    return result

                sanitized = await self._sanitize_node.process(cast("PipelineInput", item))
                if sanitized is None:
                    result["outcome"] = "dropped_sanitize"
                    result["drop_stage"] = "sanitize"
                    item_span.set_attribute("job_ftch.result", "dropped")
                    item_span.set_attribute("job_ftch.exit_stage", "sanitize")
                    return result

                result["passed_sanitize"] = True

                if self._snapshot_filter is not None:
                    snapshot_item = await self._snapshot_filter.process(cast("RawItem", sanitized))
                    sanitized = cast("PipelineInput | None", snapshot_item)
                    if sanitized is None:
                        result["outcome"] = "dropped_node"
                        result["drop_stage"] = "SnapshotFilterNode"
                        item_span.set_attribute("job_ftch.result", "dropped")
                        item_span.set_attribute("job_ftch.exit_stage", "SnapshotFilterNode")
                        return result

                current: Any = self._inject_source_run_id(sanitized, source_run_id)
                for idx, node in enumerate(self._nodes):
                    if current is None:
                        break
                    failure_stage = node.__class__.__name__
                    result["failure_stage"] = failure_stage
                    with self._tracer.start_as_current_span("pipeline.node") as node_span:
                        node_span.set_attribute("job_ftch.node", failure_stage)
                        node_span.set_attribute("job_ftch.node_index", idx)

                        next_item = await node.process(current)

                        if next_item is None:
                            result["outcome"] = "dropped_node"
                            result["drop_stage"] = failure_stage
                            node_span.set_attribute("job_ftch.node.result", "drop")
                            node_span.set_attribute("job_ftch.exit_stage", failure_stage)
                            item_span.set_attribute("job_ftch.result", "dropped")
                            item_span.set_attribute("job_ftch.exit_stage", failure_stage)
                            current = None
                            break
                        if getattr(node, "is_fan_out_stage", False):
                            if not isinstance(next_item, (list, tuple)):
                                raise TypeError(
                                    f"fan-out stage {failure_stage} must return a list or tuple"
                                )
                            spans = tuple(next_item)
                            children = (
                                self._graph_executor_children(tuple(next_item), source_run_id)
                                if getattr(node, "is_graph_executor_stage", False)
                                else await self._process_fanout_children(
                                    spans, self._nodes[idx + 1 :], source_run_id, work_budget
                                )
                            )
                            if getattr(node, "is_graph_executor_stage", False) and work_budget:
                                allowed = await self._reserve_fanout_slots(
                                    len(children), work_budget
                                )
                                children = children[:allowed]
                            result["outcome"] = "fanout"
                            result["children"] = children
                            result["fanout_limited"] = len(spans) - len(children)
                            item_span.set_attribute("job_ftch.result", "fanout")
                            item_span.set_attribute("job_ftch.fanout_count", len(children))
                            return result
                        node_span.set_attribute("job_ftch.node.result", "pass")
                    current = self._inject_source_run_id(next_item, source_run_id)

                if current is not None:
                    result["current"] = current
                    work_state = str(
                        (getattr(current, "metadata", {}) or {}).get("work_state") or ""
                    )
                    result["outcome"] = "deferred" if work_state == "deferred" else "emitted"
                    item_span.set_attribute("job_ftch.result", result["outcome"])
                    item_span.set_attribute("job_ftch.exit_stage", result["outcome"])
            except RawItemDropped as exc:
                result["outcome"] = "dropped_exc"
                result["exc"] = exc
                result["drop_reason"] = exc.reason.value
                result["drop_details"] = exc.details
                result["drop_stage"] = exc.stage
                item_span.set_attribute("job_ftch.result", "dropped")
                item_span.set_attribute("job_ftch.exit_stage", str(exc.stage or ""))
            except RawItemRejected as exc:
                result["outcome"] = "quarantined"
                result["exc"] = exc
                item_span.set_attribute("job_ftch.result", "quarantined")
            except Exception as exc:
                result["outcome"] = "failed"
                result["exc"] = exc
                item_span.set_attribute("job_ftch.result", "failed")
                item_span.set_attribute("job_ftch.exit_stage", str(result["failure_stage"]))

        return result

    def _graph_executor_children(
        self, graph_results: tuple[Any, ...], source_run_id: str
    ) -> list[dict[str, Any]]:
        """Translate graph reports to normal pipeline finalizer envelopes."""
        children: list[dict[str, Any]] = []
        for graph_result in graph_results:
            source_item = getattr(graph_result, "source_item", None)
            current = getattr(graph_result, "item", None)
            status = str(getattr(graph_result, "status", "REJECT"))
            terminal = bool(getattr(graph_result, "has_terminal_decision", False))
            terminal_reasons = tuple(
                reason
                for reason in getattr(graph_result, "terminal_reasons", ())
                if isinstance(reason, str) and reason
            )
            node_events = getattr(graph_result, "node_events", {})
            child = self._blank_item_result(current or source_item)
            child["passed_sanitize"] = True
            if isinstance(node_events, dict) and node_events:
                child["graph_node_events"] = node_events
                child["trace"] = {
                    "node_events": node_events,
                    "terminal_reasons": terminal_reasons,
                }
            if terminal and current is not None:
                if status == "DEFERRED":
                    metadata = getattr(current, "metadata", None)
                    copy = getattr(current, "model_copy", None)
                    if isinstance(metadata, dict) and callable(copy):
                        current = copy(
                            update={
                                "metadata": {
                                    **metadata,
                                    "work_state": "deferred",
                                    "deferred_reason": terminal_reasons[-1]
                                    if terminal_reasons
                                    else "graph_terminal_deferred",
                                    "decision_reasons": terminal_reasons,
                                }
                            }
                        )
                    child["outcome"] = "deferred"
                else:
                    child["outcome"] = "emitted"
                child["current"] = self._inject_source_run_id(current, source_run_id)
            else:
                child["outcome"] = "dropped_node"
                child["drop_stage"] = (
                    getattr(graph_result, "first_loss_node", None) or "GraphExecutor"
                )
                loss_event = (
                    node_events.get(child["drop_stage"])
                    if isinstance(node_events, dict) and child["drop_stage"]
                    else None
                )
                event_reason = (
                    str(loss_event.get("reason") or "").strip()
                    if isinstance(loss_event, dict)
                    else ""
                )
                if event_reason == "gate_returned_none":
                    event_reason = ""
                child["drop_reason"] = event_reason or _drop_reason(child)
            children.append(child)
        return children

    async def _process_fanout_children(
        self,
        spans: tuple[Any, ...],
        nodes: Sequence[Stage[Any, Any]],
        source_run_id: str,
        work_budget: AsyncCallBudget | None,
    ) -> list[dict[str, Any]]:
        """Run remaining stages independently for every explicit fan-out span."""
        children: list[dict[str, Any]] = []
        for span in spans:
            if work_budget and not await work_budget.try_acquire():
                break
            materialize = getattr(span, "materialize_raw_item", None)
            if not callable(materialize):
                raise TypeError("FanOutStage output must provide materialize_raw_item()")
            item = self._inject_source_run_id(materialize(), source_run_id)
            child = self._blank_item_result(item)
            child["passed_sanitize"] = True
            current: Any = item
            try:
                for node in nodes:
                    child["failure_stage"] = node.__class__.__name__
                    if getattr(node, "is_fan_out_stage", False):
                        raise TypeError("Nested FanOutStage is not supported")
                    next_item = await node.process(current)
                    if next_item is None:
                        child["outcome"] = "dropped_node"
                        child["drop_stage"] = node.__class__.__name__
                        current = None
                        break
                    current = self._inject_source_run_id(next_item, source_run_id)
                if current is not None:
                    child["current"] = current
                    work_state = str(
                        (getattr(current, "metadata", {}) or {}).get("work_state") or ""
                    )
                    child["outcome"] = "deferred" if work_state == "deferred" else "emitted"
            except RawItemDropped as exc:
                child.update(
                    outcome="dropped_exc",
                    exc=exc,
                    drop_reason=exc.reason.value,
                    drop_details=exc.details,
                    drop_stage=exc.stage,
                )
            except RawItemRejected as exc:
                child.update(outcome="quarantined", exc=exc)
            except Exception as exc:  # noqa: BLE001
                child.update(outcome="failed", exc=exc)
            children.append(child)
        return children

    async def _reserve_fanout_slots(self, count: int, budget: AsyncCallBudget) -> int:
        """Reserve global work slots for graph-executor fan-out children."""
        reserved = 0
        for _ in range(count):
            if not await budget.try_acquire():
                break
            reserved += 1
        return reserved

    async def _handle_pre_quarantined_item_worker(self, item: Any, result: dict[str, Any]) -> bool:
        if not isinstance(item, QuarantinedRawItem):
            return False
        result["outcome"] = "pre_quarantined"
        result["exc"] = item
        return True

    async def _finalize_item_result(
        self, res: dict[str, Any], summary: RunSummary, *, count_fetched: bool = True
    ) -> bool:
        """Apply one worker result to summary/sink/store. Called serially in the main loop."""
        item = res["item"]
        item_id = res["item_id"]
        source_kind = res["source_kind"]
        source_name = res["source_name"]
        processed_key = res["processed_key"]
        outcome = res["outcome"]
        finalized = False
        run_interrupted = False
        final_status = "UNKNOWN"
        trace_drop_reason: str | None = None
        trace_drop_stage: str | None = None

        source_identity = summary.source_identity_stats(source_kind, source_name)

        if count_fetched:
            # fetched counts every pulled parent observation. Fan-out children
            # are candidate work units, not additional source fetches.
            summary.fetched += 1
            summary.source_stats(source_kind).fetched += 1
            if source_identity is not None:
                source_identity.fetched += 1

        if outcome == "fanout":
            interrupted = False
            for child in res.get("children", []):
                interrupted = (
                    await self._finalize_item_result(child, summary, count_fetched=False)
                ) or interrupted
            for _ in range(int(res.get("fanout_limited", 0))):
                summary.record_source_drop(source_kind, "max_items_budget", source_name)
            return interrupted

        if outcome == "pre_quarantined":
            # Reuse the exact sequential handler so the rejected sink, quarantine
            # sink, logging, and stats stay identical across both run paths.
            await self._handle_pre_quarantined_item(item, summary)
            record_item_decision_trace(
                summary=summary,
                result=res,
                final_status="REJECT",
                drop_reason=getattr(item, "reason", None),
                drop_stage="SanitizeNode",
            )
            return False

        if outcome == "already_processed":
            summary.record_source_drop(source_kind, "already_processed", source_name)
            record_item_decision_trace(
                summary=summary,
                result=res,
                final_status="REJECT",
                drop_reason="already_processed",
                drop_stage="dedup",
            )
            return False

        if outcome == "dropped_sanitize":
            trace_drop_reason = _drop_reason(res)
            trace_drop_stage = "sanitize"
            final_status = "REJECT"
            summary.record_source_drop(source_kind, trace_drop_reason, source_name)
            finalized = True
        elif outcome == "dropped_node":
            trace_drop_reason = _drop_reason(res)
            trace_drop_stage = self._optional_str(res.get("drop_stage"))
            final_status = "REJECT"
            summary.sanitized += 1
            summary.source_stats(source_kind).sanitized += 1
            if source_identity is not None:
                source_identity.sanitized += 1
            summary.record_source_drop(source_kind, trace_drop_reason, source_name)
            summary.record_rejected()
            summary.source_stats(source_kind).record_rejected()
            if source_identity is not None:
                source_identity.record_rejected()
            if not await self._emit_rejected_item(
                item=item,
                outcome=RejectedOutcome.DROPPED,
                reason=trace_drop_reason,
                details="Pipeline node returned no item",
                stage=trace_drop_stage,
                trace=res.get("trace"),
            ):
                summary.record_failure()
                summary.source_stats(source_kind).record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
            finalized = True
        elif outcome == "emitted":
            summary.sanitized += 1
            summary.source_stats(source_kind).sanitized += 1
            if source_identity is not None:
                source_identity.sanitized += 1
            current = res["current"]
            summary.triaged += 1
            summary.source_stats(source_kind).triaged += 1
            if source_identity is not None:
                source_identity.triaged += 1
            self._record_output_stats(current, summary, source_kind, source_name)
            if isinstance(current, JobRecord) and current.routing_decision is MatchDecision.REJECT:
                final_status = "REJECT"
                trace_drop_reason = "policy_reject"
                trace_drop_stage = "DecisionNode"
                summary.record_source_drop(source_kind, "policy_reject", source_name)
                summary.record_rejected()
                summary.source_stats(source_kind).record_rejected()
                if source_identity is not None:
                    source_identity.record_rejected()
                if not await self._emit_rejected_item(
                    item=current,
                    outcome=RejectedOutcome.DROPPED,
                    reason="policy_reject",
                    details="DecisionNode selected REJECT",
                    stage="DecisionNode",
                ):
                    summary.record_failure()
                    summary.source_stats(source_kind).record_failure()
                finalized = True
                if finalized and processed_key is not None:
                    await self._settle_dedup_claims(item, commit=True)
                    await self._store.mark_processed(processed_key)
                    await self._set_run_state("pipeline.last_processed_key", processed_key)
                record_item_decision_trace(
                    summary=summary,
                    result=res,
                    final_status=final_status,
                    drop_reason=trace_drop_reason,
                    drop_stage=trace_drop_stage,
                )
                return run_interrupted
            try:
                outbox_targets = await self._enqueue_outbox(item, current)
                await self._emit_outbox_targets(current, outbox_targets)
                summary.emitted += 1
                summary.source_stats(source_kind).emitted += 1
                if source_identity is not None:
                    source_identity.emitted += 1
                decision = getattr(current, "routing_decision", None)
                final_status = str(getattr(decision, "value", decision) or "ACCEPT").upper()
                finalized = True
            except Exception as exc:
                final_status = "FAILED"
                trace_drop_reason = "sink_emit_failed"
                trace_drop_stage = self._sink.__class__.__name__
                summary.record_failure()
                summary.record_rejected()
                summary.source_stats(source_kind).record_failure()
                summary.source_stats(source_kind).record_rejected()
                if source_identity is not None:
                    source_identity.record_failure()
                    source_identity.record_rejected()
                self._logger.exception("pipeline_sink_emit_failed", item_id=item_id, error=str(exc))
                # Match sequential semantics: per-item sink.emit failures leave
                # the run in completed_with_failures, preserving successful items.
        elif outcome == "deferred":
            current = res.get("current")
            final_status = "DEFERRED"
            trace_drop_reason = str(
                (getattr(current, "metadata", {}) or {}).get("deferred_reason")
                or "evidence_missing"
            )
            trace_drop_stage = "DecisionNode"
            summary.record_deferred(trace_drop_reason)
            summary.source_stats(source_kind).record_deferred(trace_drop_reason)
            if source_identity is not None:
                source_identity.record_deferred(trace_drop_reason)
            await self._enqueue_deferred(current)
            # Deferred work must not be marked processed; the claim is released
            # below and the resolver will replay this content version.
            finalized = False
        elif outcome == "dropped_exc":
            drop_exc = res["exc"]
            res["current"] = drop_exc.item
            final_status = "REJECT"
            trace_drop_reason = str(drop_exc.reason.value)
            trace_drop_stage = self._optional_str(drop_exc.stage)
            if res["passed_sanitize"]:
                summary.sanitized += 1
                summary.source_stats(source_kind).sanitized += 1
                if source_identity is not None:
                    source_identity.sanitized += 1
            summary.record_source_drop(source_kind, trace_drop_reason, source_name)
            summary.record_rejected()
            summary.source_stats(source_kind).record_rejected()
            if source_identity is not None:
                source_identity.record_rejected()
            if drop_exc.reason.value.startswith("duplicate_"):
                summary.record_duplicate()
                summary.source_stats(source_kind).record_duplicate()
                if source_identity is not None:
                    source_identity.record_duplicate()
            if not await self._emit_rejected_item(
                item=drop_exc.item,
                outcome=RejectedOutcome.DROPPED,
                reason=drop_exc.reason.value,
                details=drop_exc.details,
                stage=drop_exc.stage,
            ):
                summary.record_failure()
                summary.source_stats(source_kind).record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
            finalized = True
        elif outcome == "quarantined":
            quarantined_exc = res["exc"]
            res["current"] = quarantined_exc.item
            final_status = "REJECT"
            trace_drop_reason = str(quarantined_exc.reason.value)
            trace_drop_stage = "SanitizeNode"
            summary.record_source_drop(source_kind, quarantined_exc.reason.value, source_name)
            summary.record_source_quarantine(source_kind, quarantined_exc.reason.value, source_name)
            summary.record_rejected()
            summary.source_stats(source_kind).record_rejected()
            if source_identity is not None:
                source_identity.record_rejected()
            if not await self._emit_rejected_item(
                item=quarantined_exc.item,
                outcome=RejectedOutcome.QUARANTINED,
                reason=quarantined_exc.reason.value,
                details=quarantined_exc.details,
                stage="SanitizeNode",
            ):
                summary.record_failure()
                summary.source_stats(source_kind).record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
            if not await self._emit_quarantined_item(quarantined_exc.to_quarantined()):
                summary.record_failure()
                summary.source_stats(source_kind).record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
            finalized = True
        elif outcome == "failed":
            failed_exc = res["exc"]
            final_status = "FAILED"
            trace_drop_reason = str(res["failure_reason"])
            trace_drop_stage = str(res["failure_stage"])
            if res["passed_sanitize"]:
                summary.sanitized += 1
                summary.source_stats(source_kind).sanitized += 1
                if source_identity is not None:
                    source_identity.sanitized += 1
            summary.record_failure()
            summary.record_rejected()
            summary.source_stats(source_kind).record_failure()
            summary.source_stats(source_kind).record_rejected()
            if source_identity is not None:
                source_identity.record_failure()
                source_identity.record_rejected()
            self._logger.exception(
                "pipeline_item_failed",
                item_id=item_id,
                reason=res["failure_reason"],
                stage=res["failure_stage"],
            )
            if not await self._emit_rejected_item(
                item=item,
                outcome=RejectedOutcome.FAILED,
                reason=res["failure_reason"],
                details=str(failed_exc) or failed_exc.__class__.__name__,
                stage=res["failure_stage"],
            ):
                summary.record_failure()
                summary.source_stats(source_kind).record_failure()
                if source_identity is not None:
                    source_identity.record_failure()
            # Transient generic exception: do not permanently consume the item.
            finalized = False

        if finalized and processed_key is not None:
            await self._settle_dedup_claims(item, commit=True)
            await self._store.mark_processed(processed_key)
            await self._set_run_state("pipeline.last_processed_key", processed_key)

        if not finalized:
            await self._settle_dedup_claims(item, commit=False)
        record_item_decision_trace(
            summary=summary,
            result=res,
            final_status=final_status,
            drop_reason=trace_drop_reason,
            drop_stage=trace_drop_stage,
        )
        return run_interrupted

    async def _settle_dedup_claims(self, item: object, *, commit: bool) -> None:
        item_id = getattr(item, "stable_id", None)
        if not item_id:
            return
        name = "commit_claim" if commit else "release_claim"
        nodes_to_notify = list(self._nodes)
        if self._snapshot_filter is not None:
            nodes_to_notify.append(self._snapshot_filter)
        for node in nodes_to_notify:
            method = getattr(node, name, None)
            if callable(method):
                await method(str(item_id))

    async def _enqueue_outbox(
        self, item: object, delivery: object
    ) -> tuple[tuple[str, str, OutboxState], ...]:
        if not isinstance(item, RawItem) or not isinstance(delivery, JobRecord):
            return ()
        enqueue = getattr(self._store, "enqueue_outbox", None)
        if not callable(enqueue):
            return ()
        content_hash = content_hash_for_raw_item(item)
        payload = delivery.model_dump(mode="json") if hasattr(delivery, "model_dump") else {}
        targets: list[tuple[str, str, OutboxState]] = []
        for target_id in self._delivery_targets:
            key = delivery_idempotency_key(
                content_hash=content_hash, decision_version="pipeline-v1", sink_name=target_id
            )
            persisted = await enqueue(
                OutboxRecord(
                    outbox_id=key,
                    observation_id=str(
                        item.metadata.get("parent_observation_id") or item.stable_id
                    ),
                    content_hash=content_hash,
                    decision_version="pipeline-v1",
                    sink_name=target_id,
                    idempotency_key=key,
                    delivery_payload=payload,
                )
            )
            targets.append((persisted.idempotency_key, target_id, persisted.state))
        return tuple(targets)

    async def _emit_outbox_targets(
        self, delivery: object, targets: tuple[tuple[str, str, OutboxState], ...]
    ) -> None:
        await self._sink.emit(cast("PipelineOutput", delivery))
        for outbox_key, target_id, state in targets:
            if state is OutboxState.DELIVERED:
                continue
            target = self._delivery_targets.get(target_id)
            if target is None:
                raise RuntimeError(f"Outbox target {target_id!r} is no longer configured")
            await target.deliver(cast("JobRecord", delivery))
            await self._store.mark_outbox_delivered(outbox_key)

    async def _recover_pending_outbox(self) -> None:
        """Replay only the still-pending leaf target for each durable record."""
        if not callable(getattr(self._store, "list_pending_outbox", None)):
            return

        async def deliver(record: OutboxRecord) -> None:
            try:
                item = JobRecord.model_validate(record.delivery_payload)
            except Exception as exc:
                raise ValueError(
                    f"Outbox payload for {record.idempotency_key} is not a JobRecord"
                ) from exc
            target = self._delivery_targets.get(record.sink_name)
            if target is None:
                raise RuntimeError(f"Outbox target {record.sink_name!r} is no longer configured")
            await target.deliver(item)

        owner_id = uuid4().hex

        async def claim(record: OutboxRecord) -> bool:
            return await self._store.acquire_dedup_claim(
                f"outbox_delivery:{record.idempotency_key}", owner_id, ttl_seconds=300
            )

        async def release(record: OutboxRecord) -> None:
            await self._store.release_dedup_claim(
                f"outbox_delivery:{record.idempotency_key}", owner_id
            )

        recovered = await recover_pending_outbox(self._store, deliver, claim=claim, release=release)
        if recovered:
            self._logger.info("pending_outbox_recovered", recovered=recovered)

    async def _enqueue_deferred(self, item: object) -> str | None:
        if not isinstance(item, JobRecord):
            return None
        metadata = item.metadata or {}
        reasons = tuple(
            str(value) for value in metadata.get("decision_reasons", ()) if str(value).strip()
        )
        missing_claim = str(metadata.get("missing_critical_claim") or "").strip()
        if missing_claim:
            reasons = (missing_claim,)
        task = ResolutionTask(
            observation_id=str(metadata.get("parent_observation_id") or item.raw_item_id),
            candidate_id=str(metadata.get("candidate_span_id") or item.raw_item_id),
            required_claims=reasons,
            resolver_name=str(metadata.get("resolver_name") or "evidence_resolver"),
            policy_version=str(metadata.get("evidence_policy_version") or "evidence-v1"),
            payload={"job_id": item.job_id, "source_name": item.source_name},
        )
        await DeferredResolverQueue(self._store).enqueue(task)
        return task.task_id

    async def _record_observation(self, item: RawItem, settings: Any) -> None:
        """Persist raw input before any suppression or mutable processing.

        Ledger writes are idempotent by ``(tenant, stable_id, content_hash)``;
        storage assigns the content version.  This keeps the source envelope
        available when a terminal decision later needs replaying under a new
        policy version.
        """
        record = getattr(self._store, "record_observation", None)
        if not callable(record):
            return
        content_hash = content_hash_for_raw_item(item)
        tenant_id = str(
            getattr(self._store, "tenant_id", None)
            or getattr(self._store, "_tenant_id", None)
            or "default"
        )
        try:
            await record(
                ObservationLedgerEntry(
                    observation_id=f"{tenant_id}:{item.stable_id}:{content_hash}",
                    tenant_id=tenant_id,
                    stable_id=item.stable_id,
                    content_hash=content_hash,
                    decision_version=str(
                        getattr(settings, "pipeline_decision_version", "pipeline-v1")
                    ),
                    raw_item=item,
                )
            )
        except ValueError:
            # A malformed envelope must still reach SanitizeNode and its
            # quarantine lane; only valid immutable observations enter ledger.
            self._logger.info("observation_ledger_skipped_invalid_raw_item")

    async def _run_concurrent(
        self, summary: RunSummary, max_items: int | None, settings: Any
    ) -> bool:
        """Run pipeline with bounded item-level concurrency.

        Workers handle the expensive I/O (LLM, HTTP, embeddings) concurrently.
        Finalization (sink.emit, mark_processed, summary counters) runs serially
        in the main coroutine — no concurrent-write races on shared state.
        """
        concurrency = self._item_concurrency
        source_run_id = summary.source_run_id
        run_interrupted = False
        pulled = 0
        work_budget = AsyncCallBudget(max_items) if max_items is not None else None
        # In-run guard against the has_processed/mark_processed TOCTOU race:
        # workers check has_processed() concurrently but mark_processed() only
        # runs in the finalizer, so two items sharing a processed_key in the same
        # window could both pass. The main loop is single-threaded, so deduping
        # keys here at task-creation time is race-free.
        seen_keys: set[str] = set()

        source_iter = self._source.fetch().__aiter__()
        pending: set[asyncio.Task[dict[str, Any]]] = set()

        async def _drain_pending(*, wait_all: bool = False) -> bool:
            interrupted = False
            return_when = asyncio.ALL_COMPLETED if wait_all else asyncio.FIRST_COMPLETED
            while pending and (wait_all or len(pending) >= concurrency):
                done, _ = await asyncio.wait(pending, return_when=return_when)
                for task in done:
                    pending.discard(task)
                    try:
                        res = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._logger.exception("pipeline_worker_task_failed", error=str(exc))
                        summary.record_failure()
                        interrupted = True
                        continue
                    if await self._finalize_item_result(res, summary):
                        interrupted = True
                if not wait_all:
                    break
            return interrupted

        try:
            while True:
                reserved_parent = False
                if work_budget is not None:
                    reserved_parent = await work_budget.try_acquire()
                    if not reserved_parent:
                        break
                try:
                    item = await source_iter.__anext__()
                except StopAsyncIteration:
                    if reserved_parent:
                        assert work_budget is not None
                        await work_budget.release()
                    break
                except Exception as exc:
                    if reserved_parent:
                        assert work_budget is not None
                        await work_budget.release()
                    summary.record_failure()
                    await self._emit_source_failure(exc, summary)
                    run_interrupted = True
                    break

                pulled += 1

                key = processed_key_for_raw_item(item) if isinstance(item, RawItem) else None
                if key is not None and key in seen_keys:
                    # Duplicate processed_key already seen this run — drop it
                    # before spawning a worker to avoid the has_processed/
                    # mark_processed TOCTOU double emit. Route through the
                    # finalizer as an already_processed drop so summary stats
                    # match the sequential path exactly.
                    dup_res = self._blank_item_result(item)
                    dup_res["outcome"] = "already_processed"
                    await self._finalize_item_result(dup_res, summary)
                    continue
                if key is not None:
                    seen_keys.add(key)

                task = asyncio.create_task(
                    self._process_item_worker(item, source_run_id or "", settings, work_budget)
                )
                pending.add(task)

                if len(pending) >= concurrency and await _drain_pending(wait_all=False):
                    run_interrupted = True

            if await _drain_pending(wait_all=True):
                run_interrupted = True

        finally:
            for t in pending:
                t.cancel()
            if pending:
                with contextlib.suppress(Exception):
                    await asyncio.gather(*pending, return_exceptions=True)
            close_iter = getattr(source_iter, "aclose", None)
            if callable(close_iter):
                await close_iter()

        return run_interrupted

    async def _handle_pre_quarantined_item(
        self,
        item: PipelineInput | QuarantinedRawItem,
        summary: RunSummary,
    ) -> bool:
        if not isinstance(item, QuarantinedRawItem):
            return False
        source_kind = item.source_kind
        source_name = item.source_name
        summary.record_source_drop(source_kind, item.reason.value, source_name)
        summary.record_source_quarantine(source_kind, item.reason.value, source_name)
        self._logger.info(
            "pipeline_source_item_quarantined",
            reason=item.reason.value,
            details=item.details,
        )
        summary.record_rejected()
        summary.source_stats(source_kind).record_rejected()
        source_identity = summary.source_identity_stats(source_kind, source_name)
        if source_identity is not None:
            source_identity.record_rejected()
        if not await self._emit_rejected_item(
            item=item,
            outcome=RejectedOutcome.QUARANTINED,
            reason=item.reason.value,
            details=item.details,
            stage="source",
        ):
            summary.record_failure()
            summary.source_stats(source_kind).record_failure()
            if source_identity is not None:
                source_identity.record_failure()
        if not await self._emit_quarantined_item(item):
            summary.record_failure()
            summary.source_stats(source_kind).record_failure()
            if source_identity is not None:
                source_identity.record_failure()
        return True

    def _source_failure_identity(self) -> tuple[str, str | None]:
        """Best-effort identity for a top-level source iterator failure."""
        source = self._source
        spec = getattr(source, "spec", None)
        if spec is not None:
            source_kind = str(getattr(spec, "type", "unknown") or "unknown")
            source_name = str(getattr(spec, "source_name", "") or "") or None
            return source_kind, source_name
        source_kind = str(getattr(source, "source_kind", "") or "unknown")
        source_name = str(getattr(source, "source_name", "") or "") or None
        return source_kind, source_name

    async def _emit_source_failure(self, exc: Exception, summary: RunSummary) -> None:
        source_kind, source_name = self._source_failure_identity()
        summary.record_source_quarantine(
            source_kind, RawItemRejectionReason.SOURCE_FETCH_ERROR.value, source_name
        )
        summary.record_rejected()
        summary.source_stats(source_kind).record_failure()
        summary.source_stats(source_kind).record_rejected()
        source_identity = summary.source_identity_stats(source_kind, source_name)
        if source_identity is not None:
            source_identity.record_failure()
            source_identity.record_rejected()
        self._logger.exception("pipeline_source_failed")
        if not await self._emit_rejected_item(
            item=QuarantinedRawItem(
                reason=RawItemRejectionReason.SOURCE_FETCH_ERROR,
                details=sanitize_string(str(exc) or exc.__class__.__name__),
                source_kind=source_kind,
                source_name=source_name,
                snapshot={
                    "error_type": exc.__class__.__name__,
                    "error_message": sanitize_string(str(exc)),
                },
            ),
            outcome=RejectedOutcome.FAILED,
            reason=RawItemRejectionReason.SOURCE_FETCH_ERROR.value,
            details=sanitize_string(str(exc) or exc.__class__.__name__),
            stage="source",
        ):
            summary.record_failure()
        if not await self._emit_quarantined_item(
            QuarantinedRawItem(
                reason=RawItemRejectionReason.SOURCE_FETCH_ERROR,
                details=sanitize_string(str(exc) or exc.__class__.__name__),
                source_kind=source_kind,
                source_name=source_name,
                snapshot={
                    "error_type": exc.__class__.__name__,
                    "error_message": sanitize_string(str(exc)),
                },
            )
        ):
            summary.record_failure()

    def _record_output_stats(
        self,
        item: object,
        summary: RunSummary,
        source_kind: object | None,
        source_name: object | None,
    ) -> None:
        if not isinstance(item, Job):
            return
        summary.extracted += 1
        summary.source_stats(source_kind).extracted += 1
        source_identity = summary.source_identity_stats(source_kind, source_name)
        if source_identity is not None:
            source_identity.extracted += 1
        if item.extraction_status is JobExtractionStatus.PARTIAL:
            summary.partial += 1
            summary.source_stats(source_kind).partial += 1
            if source_identity is not None:
                source_identity.partial += 1
        if item.review_reasons:
            summary.review += 1
            summary.source_stats(source_kind).review += 1
            if source_identity is not None:
                source_identity.review += 1

    async def _emit_quarantined_item(self, item: QuarantinedRawItem) -> bool:
        if self._quarantine_sink is None:
            return True
        try:
            await self._quarantine_sink.emit(item)
        except Exception:
            self._logger.exception("pipeline_secondary_sink_emit_failed", sink="quarantine")
            return False
        return True

    async def _emit_rejected_item(
        self,
        *,
        item: object,
        outcome: RejectedOutcome,
        reason: str,
        details: str,
        stage: str | None,
        trace: dict[str, Any] | None = None,
    ) -> bool:
        if self._rejected_sink is None:
            return True
        payload = (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else {"item": str(item)}
        )
        rejected = RejectedItem(
            outcome=outcome,
            reason=reason,
            details=sanitize_string(details) if details else details,
            stage=stage,
            item_type=item.__class__.__name__,
            source_kind=self._optional_str(getattr(item, "source_kind", None)),
            source_name=self._optional_str(getattr(item, "source_name", None)),
            stable_id=self._optional_str(getattr(item, "stable_id", None)),
            raw_item_id=self._optional_str(getattr(item, "raw_item_id", None)),
            trace=trace or {},
            snapshot=cast("dict[str, object]", payload),
        )
        try:
            await self._rejected_sink.emit(rejected)
        except Exception:
            self._logger.exception("pipeline_secondary_sink_emit_failed", sink="rejected")
            return False
        return True

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _coerce_sink(
        self,
        sink: Sink[PipelineOutput] | Sequence[Sink[PipelineOutput]],
    ) -> Sink[PipelineOutput]:
        from job_ftch.sinks.fanout import FanOutSink

        if isinstance(sink, Sequence):
            return FanOutSink(sink)
        return sink

    @staticmethod
    def _inject_source_run_id(item: object, run_id: str) -> object:
        """Return an input/output record with `metadata.source_run_id = run_id`.

        Used twice in the per-item loop: once after the node chain and once
        before the sink emit. Both call sites used to do the same
        `model_copy(update={...})` dance in place; centralising it makes the
        Raw inputs need the tag too so extraction and aggregation persist the
        same correlation id used by source logs and traces.
        """
        if not isinstance(item, (RawItem, JobRecord)):
            return item
        return item.model_copy(
            update={
                "metadata": {**item.metadata, "source_run_id": run_id},
            }
        )

    async def _flush_if_supported(self, target: object) -> None:
        flush = getattr(target, "flush", None)
        if callable(flush):
            await flush()

    async def _set_run_state(self, key: str, value: str) -> None:
        await self._store.set_run_state(key, value)

    def _record_graph_metrics(self, summary: RunSummary) -> None:
        for node in self._nodes:
            graph_hash = getattr(node, "graph_hash", None)
            metrics = getattr(node, "node_metrics", None)
            if isinstance(graph_hash, str):
                summary.graph_hash = graph_hash
            if isinstance(metrics, dict):
                summary.graph_node_metrics = {
                    str(key): dict(value)
                    for key, value in metrics.items()
                    if isinstance(value, dict)
                }
            runtime_stats = getattr(node, "runtime_stats", None)
            if callable(runtime_stats):
                for key, value in runtime_stats().items():
                    if hasattr(summary, key) and isinstance(value, int):
                        setattr(summary, key, value)
