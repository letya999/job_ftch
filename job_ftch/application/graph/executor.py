"""Small async executor with explicit parallel, background and deferred lanes."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from opentelemetry import trace

from .conditions import ConditionResult, evaluate_condition
from .contracts import (
    CompiledGraph,
    EffectMode,
    EvidenceBundle,
    EvidencePatch,
    ExecutionMode,
    RuntimeContext,
)
from .registry import build_runtime_bindings, factory, validate_params


@dataclass
class ExecutionReport:
    item: Any
    status: str = "ACCEPT"
    artifacts: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    node_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)


class GraphExecutor:
    def __init__(self, graph: CompiledGraph, *, factories: dict[str, Any] | None = None) -> None:
        self.graph = graph
        self.factories = factories or {}

    @classmethod
    def from_nodes(
        cls, graph: CompiledGraph, instances: list[Any], extra: dict[str, Any] | None = None
    ) -> GraphExecutor:
        from .registry import bind_node_instances, validate_params

        bindings_by_kind = bind_node_instances(instances)
        bindings: dict[str, Any] = {}
        configured_ids: set[str] = set()
        for node in graph.spec.nodes:
            if node.node not in bindings_by_kind:
                continue
            instance = bindings_by_kind[node.node]
            if node.params:
                effective = validate_params(node.node, node.params)
                configure = getattr(instance, "configure_graph_params", None)
                if not callable(configure):
                    raise RuntimeError(
                        f"{node.id}: bound {type(instance).__name__} cannot apply graph params"
                    )
                configured = configure(effective)
                if configured is not None:
                    instance = configured
                configured_ids.add(node.id)
            bindings[node.id] = instance
        bindings.update(extra or {})
        policy = graph.spec.metadata.get("decision_policy")
        if isinstance(policy, dict):
            for node in graph.spec.nodes:
                if node.effect != EffectMode.TERMINAL_DECISION or node.id not in bindings:
                    continue
                configure_policy = getattr(bindings[node.id], "configure_graph_policy", None)
                if not callable(configure_policy) and policy.get("mode") in {"weighted", "claims"}:
                    raise RuntimeError(f"{node.id}: terminal owner cannot apply decision_policy")
                if callable(configure_policy):
                    configure_policy(policy)
        for node in graph.spec.nodes:
            if not node.params or node.id not in bindings or node.id in configured_ids:
                continue
            effective = validate_params(node.node, node.params)
            configure = getattr(bindings[node.id], "configure_graph_params", None)
            if not callable(configure):
                raise RuntimeError(
                    f"{node.id}: bound {type(bindings[node.id]).__name__} cannot apply graph params"
                )
            configured = configure(effective)
            if configured is not None:
                bindings[node.id] = configured
        missing = [node.id for node in graph.spec.nodes if node.id not in bindings]
        if missing:
            raise RuntimeError(f"runtime graph bindings missing: {', '.join(missing)}")
        return cls(graph, factories=bindings)

    @classmethod
    def from_runtime_context(cls, graph: CompiledGraph, context: RuntimeContext) -> GraphExecutor:
        """Construct the graph through explicit typed runtime resources."""
        missing_resources = sorted(
            {target for target in graph.spec.resources.values() if target not in context.resources}
        )
        if missing_resources:
            raise RuntimeError(f"graph runtime resources missing: {', '.join(missing_resources)}")
        return cls(graph, factories=build_runtime_bindings(graph, context))

    async def run(self, item: Any) -> ExecutionReport:
        reports = await self.run_many(item)
        if len(reports) != 1:
            raise RuntimeError("fan-out graph produced multiple candidates; use run_many()")
        return reports[0]

    async def run_many(self, item: Any) -> list[ExecutionReport]:
        seen_items: list[Any] = []
        try:
            reports = await self._run_from(item, 0, seen_items)
        except Exception:
            for it in seen_items:
                await self._settle_deferred_dedup_claims(it, commit=False)
            raise
        for it in seen_items:
            await self._settle_deferred_dedup_claims(it, commit=True)
        return reports

    async def _settle_deferred_dedup_claims(self, item: Any, *, commit: bool) -> None:
        """Mirror Pipeline's claim lifecycle for declarative graph executions."""
        item_id = getattr(item, "stable_id", None)
        if not item_id:
            return
        method_name = "commit_claim" if commit else "release_claim"
        visited: set[int] = set()
        for node in self.factories.values():
            if id(node) in visited:
                continue
            visited.add(id(node))
            settle = getattr(node, method_name, None)
            if callable(settle):
                await settle(str(item_id))

    async def _run_from(
        self, item: Any, start_index: int, seen_items: list[Any] | None = None
    ) -> list[ExecutionReport]:
        if seen_items is not None:
            seen_items.append(item)
        report = ExecutionReport(item=item)
        nodes = {node.id: node for node in self.graph.spec.nodes}
        background: dict[str, asyncio.Task[Any]] = {}
        values: dict[str, Any] = {}
        index = start_index
        while index < len(self.graph.execution_order):
            node_id = self.graph.execution_order[index]
            spec = nodes[node_id]
            if spec.execution == ExecutionMode.BACKGROUND:
                snapshot = copy.deepcopy(report.item)
                background[node_id] = asyncio.create_task(self._call(spec, snapshot, report))
                index += 1
                continue
            if background and spec.execution == ExecutionMode.SEQUENTIAL:
                await self._join(background, report, values)
            if spec.execution == ExecutionMode.PARALLEL:
                group = []
                while index < len(self.graph.execution_order):
                    candidate = nodes[self.graph.execution_order[index]]
                    if candidate.execution != ExecutionMode.PARALLEL:
                        break
                    group.append(candidate)
                    index += 1
                snapshot = copy.deepcopy(report.item)
                results = await asyncio.gather(
                    *(self._call(other, snapshot, report, values) for other in group)
                )
                for result in results:
                    if result is not None and type(result) is type(report.item):
                        report.item = self._merge_payload(report.item, result)
                continue
            if spec.execution == ExecutionMode.DEFERRED:
                report.status = "DEFERRED"
                report.diagnostics.append({"node": node_id, "reason": "deferred"})
                report.node_events[node_id] = {
                    "node_id": node_id,
                    "node": spec.node,
                    "execution": str(spec.execution),
                    "effect": str(spec.effect),
                    "shadow": spec.shadow,
                    "params": validate_params(spec.node, spec.params),
                    "outcome": "deferred",
                    "reason": "deferred",
                    "elapsed_ms": 0.0,
                    "calls": 0,
                    "input_type": type(report.item).__name__,
                    "output_type": None,
                    "authority": spec.authority or "observe",
                    "wall_ms": 0.0,
                    "exclusive_ms": 0.0,
                    "wait_ms": 0.0,
                    "started_at": datetime.now(UTC).isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                return [report]
            if spec.execution == ExecutionMode.POST_ACCEPT and report.status != "ACCEPT":
                report.node_events[node_id] = {
                    "node_id": node_id,
                    "node": spec.node,
                    "execution": str(spec.execution),
                    "effect": str(spec.effect),
                    "shadow": spec.shadow,
                    "authority": spec.authority or "observe",
                    "params": validate_params(spec.node, spec.params),
                    "outcome": "skipped_post_accept",
                    "reason": "terminal_not_accept",
                    "elapsed_ms": 0.0,
                    "wall_ms": 0.0,
                    "exclusive_ms": 0.0,
                    "wait_ms": 0.0,
                    "calls": 0,
                    "input_type": type(report.item).__name__,
                    "output_type": None,
                    "started_at": datetime.now(UTC).isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                index += 1
                continue
            if spec.run_if is not None:
                condition = evaluate_condition(spec.run_if, report.item)
                should_run = condition == ConditionResult.TRUE or (
                    condition == ConditionResult.UNKNOWN and spec.on_unknown_condition == "run"
                )
                if not should_run:
                    report.node_events[node_id] = {
                        "node_id": node_id,
                        "node": spec.node,
                        "execution": str(spec.execution),
                        "effect": str(spec.effect),
                        "outcome": "skipped_condition",
                        "reason": str(condition),
                        "calls": 0,
                        "input_type": type(report.item).__name__,
                        "output_type": type(report.item).__name__,
                        "authority": spec.authority or "observe",
                    }
                    index += 1
                    continue
            call_item = (
                _attach_evidence(report.item, report.evidence)
                if spec.effect == EffectMode.TERMINAL_DECISION
                else report.item
            )
            result = await self._call(spec, call_item, report, values)
            if report.status == "REJECT" and report.diagnostics and result is None:
                return [report]
            if isinstance(result, tuple) and getattr(
                self.factories.get(spec.id), "is_fan_out_stage", False
            ):
                return [
                    child
                    for candidate in result
                    for child in await self._run_from(
                        _materialize(candidate), index + 1, seen_items
                    )
                ]
            if result is None and spec.effect == EffectMode.GATE and not spec.shadow:
                self._update_event(report, spec.id, outcome="drop", reason="gate_returned_none")
                report.status = "REJECT"
                return [report]
            if result is None and spec.effect == EffectMode.GATE and spec.shadow:
                self._update_event(
                    report, spec.id, outcome="would_drop", reason="gate_returned_none"
                )
            if spec.effect == EffectMode.TERMINAL_DECISION:
                report.status = _terminal_status(result)
                self._update_event(
                    report,
                    spec.id,
                    terminal_status=report.status,
                    terminal_reasons=_terminal_reasons(result),
                )
                typed_evidence = _typed_evidence_artifact(result)
                if typed_evidence is not None:
                    report.artifacts["typed_evidence"] = typed_evidence
            if result is not None:
                # A typed DecisionNode returns DecisionResult, but post-accept
                # stages consume the accepted JobRecord.  Preserve the record
                # as the graph payload while keeping the terminal lane in the
                # report status/events.
                assessed = getattr(result, "assessed_job", None)
                record = getattr(assessed, "record", None)
                report.item = (
                    record if spec.effect == EffectMode.TERMINAL_DECISION and record else result
                )
            index += 1
        await self._join(background, report, values)
        return [report]

    async def _join(
        self, tasks: dict[str, asyncio.Task[Any]], report: ExecutionReport, values: dict[str, Any]
    ) -> None:
        for node_id, task in list(tasks.items()):
            try:
                result = await task
                values[node_id] = result
                if result is not None and type(result) is type(report.item):
                    report.item = self._merge_payload(report.item, result)
            except Exception as exc:  # optional evidence becomes unknown
                report.diagnostics.append({"node": node_id, "reason": "unknown", "error": str(exc)})
            tasks.pop(node_id, None)

    async def _call(
        self, spec: Any, item: Any, report: ExecutionReport, values: dict[str, Any] | None = None
    ) -> Any:
        started = perf_counter()
        started_at = datetime.now(UTC)
        evidence_before = len(report.evidence.patches)
        outcome = "pass"
        reason: str | None = None
        event_details: dict[str, Any] = {}
        result: Any = None
        try:
            node = self.factories.get(spec.id) or factory(spec.node, spec.params)
            tracer = trace.get_tracer("job_ftch.graph")
            with tracer.start_as_current_span(f"graph.node.{spec.id}") as span:
                validated_params = validate_params(spec.node, spec.params)
                span.set_attribute("job_ftch.graph_hash", self.graph.graph_hash)
                span.set_attribute("job_ftch.node_id", spec.id)
                span.set_attribute("job_ftch.node_index", self.graph.execution_order.index(spec.id))
                span.set_attribute("job_ftch.node", spec.node)
                span.set_attribute(
                    "job_ftch.node.params",
                    json.dumps(validated_params, ensure_ascii=False, sort_keys=True),
                )
                span.set_attribute("job_ftch.execution", str(spec.execution))
                span.set_attribute("job_ftch.effect", str(spec.effect))
                result = await asyncio.wait_for(
                    node.process(item), timeout=(spec.timeout_ms or 30000) / 1000
                )
                decision = getattr(result, "routing_decision", None)
                if decision is not None:
                    span.set_attribute("job_ftch.terminal_status", str(decision))
                reasons = _terminal_reasons(result)
                if reasons:
                    span.set_attribute("job_ftch.terminal_reasons", reasons)
            patches = self._extract_patches(result)
            if patches:
                report.evidence = report.evidence.merge(*patches)
                report.node_events.setdefault(spec.id, {})["evidence_produced"] = [
                    {
                        "claim": patch.claim,
                        "producer": patch.producer,
                        "independence_group": patch.independence_group,
                        "features": patch.features,
                        "recommendation": patch.recommendation,
                        "reliability": patch.reliability,
                        "reason": patch.reason,
                    }
                    for patch in patches
                ]
            if values is not None:
                values[spec.id] = result
            return result
        except TimeoutError:
            outcome = "timeout"
            reason = "timeout"
            report.diagnostics.append({"node": spec.id, "reason": "timeout"})
            return None
        except Exception as exc:
            if exc.__class__.__name__ in {"RawItemDropped", "RawItemRejected"}:
                outcome = "drop"
                explicit_reason = getattr(getattr(exc, "reason", None), "value", None)
                reason = str(explicit_reason or exc.__class__.__name__)
                report.status = "REJECT"
                event_details.update(_diagnostic_metadata(getattr(exc, "item", None)))
                details = str(getattr(exc, "details", "") or "")
                if details:
                    event_details["drop_details"] = details
                report.diagnostics.append({"node": spec.id, "reason": reason, "error": str(exc)})
                return None
            if spec.on_error == "unknown" or spec.execution in (
                ExecutionMode.PARALLEL,
                ExecutionMode.BACKGROUND,
            ):
                outcome = "unknown"
                reason = exc.__class__.__name__
                report.diagnostics.append({"node": spec.id, "reason": "unknown", "error": str(exc)})
                return None
            outcome = "error"
            reason = exc.__class__.__name__
            raise
        finally:
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            report.node_stats[spec.id] = {"elapsed_ms": elapsed_ms, "calls": 1}
            previous_event = report.node_events.get(spec.id, {})
            event = {
                "node_id": spec.id,
                "node": spec.node,
                "execution": str(spec.execution),
                "effect": str(spec.effect),
                "shadow": spec.shadow,
                "params": validate_params(spec.node, spec.params),
                "outcome": outcome,
                "reason": reason,
                "elapsed_ms": elapsed_ms,
                "calls": 1,
                "input_type": type(item).__name__,
                "output_type": type(result).__name__ if result is not None else None,
                "evidence_consumed": [patch.claim for patch in report.evidence.patches]
                if spec.effect == EffectMode.TERMINAL_DECISION
                else [],
                "evidence_count_before": evidence_before,
                "evidence_count_after": len(report.evidence.patches),
                "authority": spec.authority or _authority_for_effect(spec.effect, spec.shadow),
                "wall_ms": elapsed_ms,
                "exclusive_ms": elapsed_ms,
                "wait_ms": 0.0,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
            if "evidence_produced" in previous_event:
                event["evidence_produced"] = previous_event["evidence_produced"]
            event.update(event_details)
            report.node_events[spec.id] = event

    @staticmethod
    def _update_event(report: ExecutionReport, node_id: str, **updates: Any) -> None:
        event = report.node_events.get(node_id)
        if event is not None:
            event.update(updates)

    @staticmethod
    def _merge_payload(current: Any, incoming: Any) -> Any:
        """Merge same-type model metadata; never let completion order discard evidence."""
        current_meta = getattr(current, "metadata", None)
        incoming_meta = getattr(incoming, "metadata", None)
        if isinstance(current_meta, dict) and isinstance(incoming_meta, dict):
            merged = {**current_meta, **incoming_meta}
            model_copy = getattr(current, "model_copy", None)
            if callable(model_copy):
                return model_copy(update={"metadata": merged})
            try:
                current.metadata = merged
                return current
            except Exception:
                return incoming
        return incoming

    @staticmethod
    def _extract_patches(result: Any) -> tuple[EvidencePatch, ...]:
        if isinstance(result, EvidencePatch):
            return (result,)
        if isinstance(result, EvidenceBundle):
            return result.patches
        patches = getattr(result, "evidence_patches", ())
        if isinstance(patches, (list, tuple)) and all(
            isinstance(item, EvidencePatch) for item in patches
        ):
            return tuple(patches)
        return ()


def _terminal_status(result: Any) -> str:
    if result is None:
        return "REJECT"
    state = getattr(result, "work_state", None)
    if str(state).lower().endswith("deferred"):
        return "DEFERRED"
    decision = getattr(result, "routing_decision", None)
    if decision is None and isinstance(result, dict):
        decision = result.get("routing_decision") or result.get("decision")
    return str(decision or "REVIEW").upper()


def _terminal_reasons(result: Any) -> list[str]:
    """Expose terminal-policy reasons in replay traces without changing routing."""
    reasons = getattr(result, "reasons", ())
    if not isinstance(reasons, (list, tuple)):
        return []
    return [str(reason) for reason in reasons if isinstance(reason, str) and reason]


def _typed_evidence_artifact(result: Any) -> dict[str, Any] | None:
    """Preserve typed decision inputs for an offline-only calibration replay.

    ``ExecutionReport.item`` intentionally becomes the accepted/rejected record
    after a terminal node, so the assessed payload would otherwise be lost.
    This artifact contains only the typed atoms and their aggregate assessments;
    it does not copy the raw posting text or embedding vectors.
    """
    assessed = getattr(result, "assessed_job", None)
    if assessed is None:
        return None

    def dump(value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        return model_dump(mode="json") if callable(model_dump) else None

    atoms = [encoded for atom in getattr(assessed, "evidence", ()) if (encoded := dump(atom))]
    assessments = [
        encoded
        for assessment in getattr(assessed, "assessments", ())
        if (encoded := dump(assessment))
    ]
    return {
        "policy_version": str(getattr(assessed, "policy_version", "unknown")),
        "atoms": atoms,
        "assessments": assessments,
        "degradation_reasons": [
            str(reason) for reason in getattr(assessed, "degradation_reasons", ())
        ],
    }


def _authority_for_effect(effect: str, shadow: bool) -> str:
    if shadow:
        return "shadow"
    if effect == EffectMode.GATE:
        return "gate"
    if effect == EffectMode.TERMINAL_DECISION:
        return "terminal"
    return "observe"


def _attach_evidence(item: Any, bundle: EvidenceBundle) -> Any:
    """Expose immutable evidence to the terminal owner without copying text/vectors."""
    if not bundle.patches:
        return item
    model_copy = getattr(item, "model_copy", None)
    metadata = getattr(item, "metadata", None)
    if not callable(model_copy) or not isinstance(metadata, dict):
        return item
    encoded = [
        {
            "claim": patch.claim,
            "producer": patch.producer,
            "independence_group": patch.independence_group,
            "features": patch.features,
            "recommendation": patch.recommendation,
            "reliability": patch.reliability,
            "reason": patch.reason,
        }
        for patch in bundle.patches
    ]
    return model_copy(update={"metadata": {**metadata, "_graph_evidence": encoded}})


def _diagnostic_metadata(item: Any) -> dict[str, Any]:
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    keys = (
        "relevance_prefilter_score",
        "relevance_prefilter_threshold",
        "relevance_prefilter_decision",
        "relevance_prefilter_model_version",
        "relevance_prefilter_degradation",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def _materialize(candidate: Any) -> Any:
    materialize = getattr(candidate, "materialize_raw_item", None)
    if materialize is None:
        raise TypeError("FanOutStage output must provide materialize_raw_item()")
    return materialize()
