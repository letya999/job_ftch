"""Bridge a compiled graph into the existing pipeline finalization lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_ftch.application.dedup_settlement import DedupSettlement
    from job_ftch.application.graph.executor import GraphExecutor


@dataclass(frozen=True)
class GraphPipelineResult:
    """One candidate result returned to ``Pipeline`` for sink finalization."""

    source_item: Any
    item: Any
    status: str
    first_loss_node: str | None
    has_terminal_decision: bool
    terminal_reasons: tuple[str, ...]
    node_events: dict[str, dict[str, Any]]


class GraphPipelineStage:
    """Run one typed graph while leaving delivery/claims to ``Pipeline``.

    The graph owns typed processing and the terminal decision. The surrounding
    pipeline still owns source snapshots, rejected/quarantine sinks, outbox,
    and processed-key lifecycle.
    """

    is_fan_out_stage = True
    is_graph_executor_stage = True

    def __init__(self, executor: GraphExecutor) -> None:
        self._executor = executor
        graph = getattr(executor, "graph", None)
        self.graph_hash = str(getattr(graph, "graph_hash", "unknown"))
        self.node_metrics: dict[str, dict[str, Any]] = {}

    def settlement_participants(self) -> tuple[DedupSettlement, ...]:
        return self._executor.settlement_participants()

    async def process(self, item: Any) -> tuple[GraphPipelineResult, ...]:
        reports = await self._executor.run_many(item)
        results: list[GraphPipelineResult] = []
        for report in reports:
            self._record_node_metrics(report.node_events)
            first_loss = next(
                (
                    node_id
                    for node_id, event in report.node_events.items()
                    if event.get("outcome") in {"drop", "error", "timeout"}
                ),
                None,
            )
            has_terminal = any(
                event.get("effect") == "terminal_decision" for event in report.node_events.values()
            )
            terminal_reasons = tuple(
                str(reason)
                for event in report.node_events.values()
                if event.get("effect") == "terminal_decision"
                for reason in event.get("terminal_reasons", ())
                if isinstance(reason, str) and reason
            )
            results.append(
                GraphPipelineResult(
                    source_item=item,
                    item=report.item,
                    status=report.status,
                    first_loss_node=first_loss,
                    has_terminal_decision=has_terminal,
                    terminal_reasons=terminal_reasons,
                    node_events=report.node_events,
                )
            )
        return tuple(results)

    def _record_node_metrics(self, events: dict[str, dict[str, Any]]) -> None:
        for node_id, event in events.items():
            metric = self.node_metrics.setdefault(
                node_id,
                {
                    "calls": 0,
                    "elapsed_ms": 0.0,
                    "outcomes": {},
                    "terminal_statuses": {},
                    "terminal_reasons": {},
                },
            )
            metric["calls"] += int(event.get("calls", 0) or 0)
            metric["elapsed_ms"] = round(
                float(metric["elapsed_ms"]) + float(event.get("elapsed_ms", 0.0) or 0.0),
                3,
            )
            outcome = str(event.get("outcome") or "unknown")
            outcomes = metric["outcomes"]
            outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
            terminal_status = str(event.get("terminal_status") or "").strip().upper()
            if terminal_status:
                statuses = metric["terminal_statuses"]
                statuses[terminal_status] = int(statuses.get(terminal_status, 0)) + 1
            reasons = metric["terminal_reasons"]
            for reason in event.get("terminal_reasons", ()):
                key = str(reason)
                reasons[key] = int(reasons.get(key, 0)) + 1

    def runtime_stats(self) -> dict[str, int]:
        """Aggregate runtime-node counters for the legacy RunSummary fields."""
        merged: dict[str, int] = {}
        seen: set[int] = set()
        for instance in self._executor.factories.values():
            if id(instance) in seen:
                continue
            seen.add(id(instance))
            stats = getattr(instance, "stats", None)
            if not isinstance(stats, dict):
                continue
            for key, value in stats.items():
                if isinstance(value, int):
                    merged[str(key)] = merged.get(str(key), 0) + value
        return merged
