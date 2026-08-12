# Composition root — exempt from application→infrastructure import ban per ADR-039.
"""Programmatic pipeline builder and settings-backed runtime helpers."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.concurrency import (
    estimate_source_work_units,
    resolve_pipeline_item_concurrency,
    resolve_source_fetch_concurrency,
    resolve_source_preparation_concurrency,
)
from job_ftch.application.contracts import (
    AuthProvider,
    BgeMThreeProviderPort,
    ClassifierProvider,
    DeliveryTarget,
    JobGroupStore,
    LLMProvider,
    SanitizingNode,
    SearchBackend,
    Sink,
    Source,
    Stage,
    Store,
)
from job_ftch.application.filter_profile_loader import (
    load_profile_catalog as load_catalog_from_path,
)
from job_ftch.application.logging import configure_logging
from job_ftch.application.pipeline import Pipeline, RunSummary
from job_ftch.application.registry import (
    create_job_group_store,
    create_job_group_store_with_fallback,
    create_llm,
    create_search_backend,
    create_sink,
    create_source,
    create_source_from_spec,
    create_store,
    create_store_with_fallback,
)
from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.application.source_loader import load_sources
from job_ftch.config import Settings, get_settings, resolve_outcome_lane_backend
from job_ftch.domain import (
    JobRecord,
    MatchDecision,
    OntologySnapshot,
    ProfileCatalog,
    QuarantinedRawItem,
    RawItem,
    RejectedItem,
    TenantConfig,
)
from job_ftch.domain.models import SkillTag
from job_ftch.domain.source_spec import SourceSpec
from job_ftch.infrastructure.observability.telemetry import configure_telemetry
from job_ftch.nodes import (
    CompensationParsingNode,
    CompletenessGateNode,
    DedupNode,
    EvidenceDecisionNode,
    ExtractionNode,
    ExtractionValidationNode,
    JobAggregationNode,
    JobLifecycleNode,
    JobValidationNode,
    LocationWorkModeNormalizationNode,
    QualityScoringNode,
    SanitizeNode,
    SkillNormalizationNode,
    TitleCompanyNormalizationNode,
)
from job_ftch.nodes.accept_template_presentation import AcceptTemplatePresentationNode
from job_ftch.nodes.full_extraction import FullExtractionNode
from job_ftch.nodes.garbage_filter import GarbageFilterNode
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.language_context import SourceContextNode
from job_ftch.nodes.lexical_evidence import LexicalEvidenceNode
from job_ftch.nodes.llm_relevance_classification import (
    LLMRelevanceClassificationNode,
    LLMRelevanceEvidenceNode,
)
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.post_accept_enrichment import PostAcceptEnrichment
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.risk import RiskScoringNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode
from job_ftch.nodes.snapshot_filter import SnapshotFilterNode
from job_ftch.nodes.tfidf_logreg_prefilter import TfidfLogregRelevancePrefilterNode
from job_ftch.sinks import CountedSink, FailureTolerantSink, FanOutSink, RoutingSink
from job_ftch.sinks.null_sink import NullSink

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _validate_pipeline_graph(settings: Settings, nodes: Sequence[Stage[Any, Any]]) -> None:
    """Reject legacy reranker thresholds from the terminal policy path."""
    reranker_enabled = (
        settings.routing_reranker_accept_threshold is not None
        or settings.routing_reranker_review_threshold is not None
    )
    if reranker_enabled:
        raise ValueError(
            "Reranker routing thresholds are resolver-only and cannot be used "
            "by the terminal evidence decision policy."
        )
    names = [type(node).__name__ for node in nodes]
    if names.count("EvidenceDecisionNode") != 1:
        raise ValueError("runtime graph must contain exactly one EvidenceDecisionNode")
    forbidden = {
        "RoutingNode",
        "ParallelScoringNode",
        "RerankerNode",
        "FullExtractionNode",
        "PresentableTextNode",
        "EmbeddingNode",
    }
    legacy = sorted(forbidden.intersection(names))
    if legacy:
        raise ValueError(f"legacy policy stages are not allowed in runtime graph: {legacy}")


def _estimate_relevance_window_coverage(settings: Settings) -> str:
    low = round(settings.llm_relevance_low_threshold, 3)
    high = round(settings.llm_relevance_high_threshold, 3)
    if low == 0.2 and high == 0.55:
        return "cascade"
    if low == 0.1 and high == 0.99:
        return "judge-dominant"
    width = high - low
    if width >= 0.7:
        return "judge-heavy-custom"
    if width <= 0.2:
        return "narrow-custom"
    return "custom"


def _log_relevance_window(settings: Settings) -> None:
    structlog.get_logger("job_ftch.builder").info(
        "relevance_judge_window",
        low_threshold=settings.llm_relevance_low_threshold,
        high_threshold=settings.llm_relevance_high_threshold,
        routing_accept_threshold=settings.routing_accept_threshold,
        expected_judge_coverage=_estimate_relevance_window_coverage(settings),
    )


def _log_active_shot_backend(
    *,
    backend: str,
    collection: str,
    source_mode: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> None:
    structlog.get_logger("job_ftch.builder").info(
        "relevance_shot_backend_active",
        backend=backend,
        collection=collection,
        source_mode=source_mode,
        tenant_id=tenant_id,
        user_id=user_id,
    )


@dataclass(frozen=True)
class _ShotLoadPlan:
    collection: str
    role_prefix: str | None
    tenant_id: str | None
    user_id: str | None


def _resolve_shot_load_plans(
    settings: Settings,
    *,
    tenant_id: str,
    user_id: str | None,
) -> list[_ShotLoadPlan]:
    mode = str(getattr(settings, "relevance_shot_source_mode", "user_db_only")).strip().lower()
    live_collection = settings.relevance_shot_collection_bgem3
    seed_collection = getattr(
        settings,
        "relevance_shot_seed_collection_bgem3",
        "profile_shots_bgem3_seed",
    )
    role_prefix = f"user:{user_id}@tenant:{tenant_id}:" if user_id else None
    if mode == "user_db_only":
        if not user_id:
            msg = "relevance_shot_source_mode='user_db_only' requires user_id"
            raise ValueError(msg)
        return [
            _ShotLoadPlan(
                collection=live_collection,
                role_prefix=role_prefix,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        ]
    if mode == "tenant_qdrant_only":
        return [
            _ShotLoadPlan(
                collection=live_collection,
                role_prefix=None,
                tenant_id=tenant_id,
                user_id=None,
            )
        ]
    if mode == "seed_fixture_only":
        return [
            _ShotLoadPlan(
                collection=seed_collection,
                role_prefix=None,
                tenant_id=None,
                user_id=None,
            )
        ]
    if mode == "mixed_debug":
        return [
            _ShotLoadPlan(
                collection=live_collection,
                role_prefix=role_prefix,
                tenant_id=tenant_id,
                user_id=user_id,
            ),
            _ShotLoadPlan(
                collection=seed_collection,
                role_prefix=None,
                tenant_id=None,
                user_id=None,
            ),
        ]
    msg = f"Unsupported relevance_shot_source_mode: {mode!r}"
    raise ValueError(msg)


class PipelineBuilder:
    """Builds pipelines programmatically for library and adapter use."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._settings = settings
        self._source_specs: list[SourceSpec] = []
        self._source_instance: Source[RawItem] | None = None
        self._auth_provider: AuthProvider | None = None
        self._stages: list[Stage[Any, Any]] = []
        self._sinks: list[Sink[JobRecord]] = []
        self._delivery_targets: list[DeliveryTarget[JobRecord]] = []
        self._store: Store | None = None
        self._quarantine_sink: Sink[QuarantinedRawItem] | None = None
        self._rejected_sink: Sink[RejectedItem] | None = None
        self._schedule_interval_seconds: int | None = None
        self._default_max_items: int | None = None
        self._source_fetch_concurrency: int = 1
        self._source_pool_dynamic_enabled = settings.source_pool_dynamic_enabled
        self._source_soft_deadline_seconds = settings.source_soft_deadline_seconds
        self._source_hard_deadline_seconds = settings.source_hard_deadline_seconds
        self._source_overflow_concurrency = settings.source_overflow_concurrency
        self._source_hard_cancel_grace_seconds = settings.source_hard_cancel_grace_seconds
        self._source_pool_adaptive_resize: bool = False
        self._source_fetch_concurrency_max: int = 16
        self._pipeline_item_concurrency: int = 1
        self._output_sink: CountedSink[JobRecord] | None = None
        self._review_sink: CountedSink[JobRecord] | None = None
        self._posting_sink: CountedSink[JobRecord] | None = None
        self._rejected_counted: CountedSink[RejectedItem] | None = None
        self._job_group_store: JobGroupStore | None = None
        self._profile_name: str | None = None
        self._output_path: Path | None = None
        # Per-run identity (ADR-031 / ADR-036). When set, build_nodes() adds a
        # SnapshotFilterNode as the 2nd stage (after SanitizeNode) and the
        # Pipeline orchestrator calls save_and_purge() at the end of run().
        self._run_id: str | None = None
        self._tenant_id: str = "default"
        self._decision_version: str = settings.pipeline_decision_version
        self._snapshot_fail_open = settings.snapshot_fail_open

    def clone(self) -> PipelineBuilder:
        cloned = self.__class__(settings=self._settings)
        cloned._source_specs = list(self._source_specs)
        cloned._source_instance = self._source_instance
        cloned._auth_provider = self._auth_provider
        cloned._stages = list(self._stages)
        cloned._sinks = list(self._sinks)
        cloned._delivery_targets = list(self._delivery_targets)
        cloned._store = self._store
        cloned._quarantine_sink = self._quarantine_sink
        cloned._rejected_sink = self._rejected_sink
        cloned._schedule_interval_seconds = self._schedule_interval_seconds
        cloned._default_max_items = self._default_max_items
        cloned._source_fetch_concurrency = self._source_fetch_concurrency
        cloned._source_pool_dynamic_enabled = self._source_pool_dynamic_enabled
        cloned._source_soft_deadline_seconds = self._source_soft_deadline_seconds
        cloned._source_hard_deadline_seconds = self._source_hard_deadline_seconds
        cloned._source_overflow_concurrency = self._source_overflow_concurrency
        cloned._source_hard_cancel_grace_seconds = self._source_hard_cancel_grace_seconds
        cloned._source_pool_adaptive_resize = self._source_pool_adaptive_resize
        cloned._source_fetch_concurrency_max = self._source_fetch_concurrency_max
        cloned._pipeline_item_concurrency = self._pipeline_item_concurrency
        cloned._output_sink = self._output_sink
        cloned._review_sink = self._review_sink
        cloned._posting_sink = self._posting_sink
        cloned._rejected_counted = self._rejected_counted
        cloned._job_group_store = self._job_group_store
        cloned._profile_name = self._profile_name
        cloned._output_path = self._output_path
        cloned._decision_version = self._decision_version
        cloned._snapshot_fail_open = self._snapshot_fail_open
        return cloned

    def source(self, spec: SourceSpec) -> PipelineBuilder:
        self._source_specs.append(spec)
        self._source_instance = None
        return self

    def sources(self, specs: list[SourceSpec]) -> PipelineBuilder:
        self._source_specs = list(specs)
        self._source_instance = None
        return self

    def auth(self, provider: AuthProvider) -> PipelineBuilder:
        self._auth_provider = provider
        return self

    def set_source_fetch_concurrency(self, value: int) -> PipelineBuilder:
        self._source_fetch_concurrency = value
        return self

    def set_source_pool_runtime(
        self,
        *,
        dynamic_enabled: bool,
        soft_deadline_seconds: float,
        hard_deadline_seconds: float,
        overflow_concurrency: int,
        hard_cancel_grace_seconds: float,
        adaptive_resize: bool,
        concurrency_max: int,
    ) -> PipelineBuilder:
        self._source_pool_dynamic_enabled = dynamic_enabled
        self._source_soft_deadline_seconds = soft_deadline_seconds
        self._source_hard_deadline_seconds = hard_deadline_seconds
        self._source_overflow_concurrency = overflow_concurrency
        self._source_hard_cancel_grace_seconds = hard_cancel_grace_seconds
        self._source_pool_adaptive_resize = adaptive_resize
        self._source_fetch_concurrency_max = concurrency_max
        return self

    def set_pipeline_item_concurrency(self, value: int) -> PipelineBuilder:
        self._pipeline_item_concurrency = value
        return self

    def stage(self, node: Stage[Any, Any]) -> PipelineBuilder:
        self._stages.append(node)
        return self

    def sink(self, sink: Sink[JobRecord]) -> PipelineBuilder:
        self._sinks.append(sink)
        return self

    def clear_sinks(self) -> PipelineBuilder:
        self._sinks = []
        return self

    def store(self, store: Store) -> PipelineBuilder:
        self._store = store
        return self

    def schedule(self, interval_seconds: int) -> PipelineBuilder:
        if interval_seconds <= 0:
            msg = "schedule interval must be > 0"
            raise ValueError(msg)
        self._schedule_interval_seconds = interval_seconds
        return self

    def with_runtime_source(self, source: Source[RawItem]) -> PipelineBuilder:
        self._source_instance = source
        self._source_specs = []
        return self

    def with_quarantine_sink(self, sink: Sink[QuarantinedRawItem]) -> PipelineBuilder:
        self._quarantine_sink = sink
        return self

    def with_rejected_sink(
        self, sink: Sink[RejectedItem], counted: CountedSink[RejectedItem] | None = None
    ) -> PipelineBuilder:
        self._rejected_sink = sink
        self._rejected_counted = counted
        return self

    def with_snapshot_filter(
        self,
        run_id: str | None = None,
        *,
        tenant_id: str = "default",
        snapshot_filter: SnapshotFilterNode | None = None,
    ) -> PipelineBuilder:
        """Enable the run-based SnapshotFilterNode (ADR-031 + ADR-036).

        When called (even with `run_id=None`), build_nodes() will include a
        SnapshotFilterNode as the 2nd stage and the Pipeline orchestrator
        will call `await node.save_and_purge()` at the end of `run()`. If
        `run_id` is omitted, run_pipeline_from_settings() generates a fresh
        uuid4 hex string at run time.
        """
        self._run_id = run_id
        self._tenant_id = tenant_id
        if snapshot_filter is not None:
            self.stage(snapshot_filter)
        return self

    def set_default_max_items(self, value: int | None) -> PipelineBuilder:
        self._default_max_items = value
        return self

    def set_summary_context(
        self,
        *,
        output_sink: CountedSink[JobRecord] | None = None,
        review_sink: CountedSink[JobRecord] | None = None,
        posting_sink: CountedSink[JobRecord] | None = None,
        job_group_store: JobGroupStore | None = None,
        profile_name: str | None = None,
        output_path: Path | None = None,
    ) -> PipelineBuilder:
        self._output_sink = output_sink
        self._review_sink = review_sink
        self._posting_sink = posting_sink
        self._job_group_store = job_group_store
        self._profile_name = profile_name
        self._output_path = output_path
        return self

    def with_delivery_targets(
        self, targets: Sequence[DeliveryTarget[JobRecord]]
    ) -> PipelineBuilder:
        self._delivery_targets = list(targets)
        return self

    @property
    def schedule_interval_seconds(self) -> int | None:
        return self._schedule_interval_seconds

    @property
    def source_fetch_concurrency(self) -> int:
        return self._source_fetch_concurrency

    @property
    def pipeline_item_concurrency(self) -> int:
        return self._pipeline_item_concurrency

    def get_store(self) -> Store:
        if self._store is None:
            msg = "PipelineBuilder requires a store before build()."
            raise ValueError(msg)
        return self._store

    def build(self) -> Pipeline[RawItem, JobRecord]:
        if self._source_instance is None and not self._source_specs:
            msg = "PipelineBuilder requires at least one source."
            raise ValueError(msg)
        if not self._stages:
            msg = "PipelineBuilder requires at least one stage."
            raise ValueError(msg)
        first = self._stages[0]
        if not (isinstance(first, SanitizeNode) or getattr(first, "_is_sanitizer", False)):
            msg = (
                "The first pipeline stage must be a SanitizeNode (or a custom "
                "class with `_is_sanitizer = True`). Got "
                f"{type(first).__name__}. "
                "Subclass SanitizeNode or set the marker on your custom stage."
            )
            raise ValueError(msg)
        if not self._sinks:
            msg = "PipelineBuilder requires at least one sink."
            raise ValueError(msg)

        source = self._source_instance or self._build_source_from_specs()
        sanitize_node = cast(SanitizingNode[RawItem], self._stages[0])
        processing_nodes = list(self._stages[1:])
        # When with_snapshot_filter() was called, the SnapshotFilterNode
        # is the 2nd stage (immediately after SanitizeNode). We construct
        # it here if the caller asked for one but did not pass it as a
        # stage explicitly; otherwise we look for it in processing_nodes
        # and pull it out so the Pipeline orchestrator can call
        # save_and_purge() at the end.
        snapshot_filter: SnapshotFilterNode | None = None
        already_has_filter = processing_nodes and isinstance(
            processing_nodes[0], SnapshotFilterNode
        )
        if self._run_id is not None and not already_has_filter:
            snapshot_filter = SnapshotFilterNode(
                self.get_store(),
                tenant_id=self._tenant_id,
                run_id=self._run_id,
                fail_open=self._snapshot_fail_open,
            )
            processing_nodes.insert(0, snapshot_filter)
        if processing_nodes and isinstance(processing_nodes[0], SnapshotFilterNode):
            snapshot_filter = cast(SnapshotFilterNode, processing_nodes.pop(0))
        return Pipeline(
            source=source,
            sanitize_node=sanitize_node,
            nodes=processing_nodes,
            sink=self._sinks[0] if len(self._sinks) == 1 else FanOutSink(self._sinks),
            store=self.get_store(),
            quarantine_sink=self._quarantine_sink,
            rejected_sink=self._rejected_sink,
            snapshot_filter=snapshot_filter,
            pipeline_item_concurrency=self._pipeline_item_concurrency,
            source_run_id=self._run_id,
            delivery_targets=self._delivery_targets,
            decision_version=self._decision_version,
            tenant_id=self._tenant_id,
            settings=self._settings,
        )

    async def run_async(self, *, max_items: int | None = None) -> RunSummary:
        pipeline = self.build()
        await self.get_store().set_run_state(
            "pipeline.source_fetch_concurrency",
            str(self._source_fetch_concurrency),
        )
        summary = await pipeline.run(max_items=max_items or self._default_max_items)
        self._apply_summary_context(summary)
        return summary

    def _apply_summary_context(self, summary: RunSummary) -> None:
        summary.applied_profile = self._profile_name or "default"
        if self._output_sink is not None:
            summary.emitted = self._output_sink.emit_count
            for source_stats in summary.by_source_kind.values():
                source_stats.emitted = 0
            for source_stats in summary.by_source_id.values():
                source_stats.emitted = 0
            for source_kind, count in self._output_sink.by_source_kind.items():
                summary.source_stats(source_kind).emitted = count
            for source_id, count in self._output_sink.by_source_id.items():
                source_kind, _, source_name = source_id.partition(":")
                source_identity = summary.source_identity_stats(source_kind, source_name)
                if source_identity is not None:
                    source_identity.emitted = count
        if self._job_group_store is not None:
            summary.new_groups_created = getattr(self._job_group_store, "new_groups_created", 0)
            summary.merged_into_group = getattr(self._job_group_store, "merged_into_group", 0)
            for source_kind, count in getattr(
                self._job_group_store, "by_source_kind_new", {}
            ).items():
                summary.source_stats(source_kind).new_groups_created = count
            for source_kind, count in getattr(
                self._job_group_store, "by_source_kind_merged", {}
            ).items():
                summary.source_stats(source_kind).merged_into_group = count
        if self._review_sink is not None:
            # `review` is a routing conversion, not the count of records that
            # merely carry completeness/quality review reasons.
            summary.review = self._review_sink.emit_count
            for source_stats in summary.by_source_kind.values():
                source_stats.review = 0
            for source_stats in summary.by_source_id.values():
                source_stats.review = 0
            for source_kind, count in self._review_sink.by_source_kind.items():
                summary.source_stats(source_kind).review = count
            for source_id, count in self._review_sink.by_source_id.items():
                source_kind, _, source_name = source_id.partition(":")
                source_identity = summary.source_identity_stats(source_kind, source_name)
                if source_identity is not None:
                    source_identity.review = count
        if self._posting_sink is not None:
            summary.posted = self._posting_sink.emit_count
            for source_stats in summary.by_source_kind.values():
                source_stats.posted = 0
            for source_stats in summary.by_source_id.values():
                source_stats.posted = 0
            for source_kind, count in self._posting_sink.by_source_kind.items():
                summary.source_stats(source_kind).posted = count
            for source_id, count in self._posting_sink.by_source_id.items():
                source_kind, _, source_name = source_id.partition(":")
                source_identity = summary.source_identity_stats(source_kind, source_name)
                if source_identity is not None:
                    source_identity.posted = count
        if self._rejected_counted is not None:
            summary.rejected = self._rejected_counted.emit_count
        if self._output_path is not None:
            structlog.get_logger("job_ftch.cli").info(
                "routing_sinks_complete",
                output_path=str(self._output_path),
                run_id=summary.source_run_id,
                fetched=summary.fetched,
                accepted=summary.emitted,
                review=summary.review,
                rejected=summary.rejected,
                deferred=summary.deferred,
                failed=summary.failed,
            )

    def _build_source_from_specs(self) -> Source[RawItem]:
        from job_ftch.infrastructure.sources.composite import CompositeSource

        child_sources = [
            cast(
                Source[RawItem],
                create_source_from_spec(spec, self._auth_provider, store=self._store),
            )
            for spec in self._source_specs
        ]
        return CompositeSource(
            child_sources,
            concurrency=self._source_fetch_concurrency,
            dynamic_enabled=self._source_pool_dynamic_enabled,
            soft_deadline_seconds=self._source_soft_deadline_seconds,
            hard_deadline_seconds=self._source_hard_deadline_seconds,
            overflow_concurrency=self._source_overflow_concurrency,
            hard_cancel_grace_seconds=self._source_hard_cancel_grace_seconds,
            adaptive_resize=self._source_pool_adaptive_resize,
            concurrency_max=self._source_fetch_concurrency_max,
        )


def load_tenant_config(path: Path) -> TenantConfig:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            msg = "PyYAML is required to load tenant YAML config files."
            raise RuntimeError(msg) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return TenantConfig.model_validate(data)


_DEPRECATED_TENANT_MODEL_KEYS: tuple[str, ...] = (
    "llm_backend",
    "openai_model",
    "relevance_llm_model",
    "embedding_enabled",
    "embedding_provider",
    "embedding_model",
    "embedding_dimensions",
)


def tenant_model_overrides(tenant: TenantConfig) -> dict[str, Any]:
    """Collect tenant model overrides that are still allowed to apply.

    Model and LLM selection is runtime-YAML policy. A tenant sits above env and
    runtime YAML in the precedence chain, so leaving these keys live means a
    tenant file silently outranks the evaluated recipe. They stay parsed for
    backward compatibility but are dropped unless the tenant opts in.

    Args:
        tenant: Loaded tenant configuration.

    Returns:
        Mapping of setting name to override value; empty when the tenant has
        not opted in via ``allow_tenant_model_override``.
    """
    present = {
        key: getattr(tenant, key)
        for key in _DEPRECATED_TENANT_MODEL_KEYS
        if getattr(tenant, key, None) is not None
    }
    if not present:
        return {}
    if tenant.allow_tenant_model_override:
        return present
    logger = structlog.get_logger(__name__)
    for key, value in present.items():
        logger.warning(
            "tenant_model_override_ignored",
            tenant_id=tenant.tenant_id,
            key=key,
            value=value,
        )
    return {}


def tenant_to_settings(tenant: TenantConfig, base_settings: Settings | None = None) -> Settings:
    base = (base_settings or get_settings()).model_dump(mode="python")
    tenant_id = tenant.tenant_id
    base.update(
        {
            "tenant_id": tenant_id,
            "tenant_display_name": tenant.display_name,
            "source_backend": tenant.source_backend,
            "sink_backend": (
                tenant.output.backend or tenant.sink_backend or base.get("sink_backend")
            ),
            "store_backend": (
                tenant.store_backend
                if tenant.store_backend is not None
                else base.get("store_backend")
            ),
            "job_group_store_backend": (
                tenant.job_group_store_backend
                if tenant.job_group_store_backend is not None
                else base.get("job_group_store_backend")
            ),
            "posting_backend": (
                tenant.posting_backend
                if tenant.posting_backend is not None
                else base.get("posting_backend")
            ),
            "notify_mode": tenant.notify_mode,
            "dry_run": tenant.dry_run,
            "pipeline_max_items_per_run": tenant.pipeline_max_items_per_run,
            "source_fetch_concurrency": (
                tenant.source_fetch_concurrency
                if tenant.source_fetch_concurrency is not None
                else base.get("source_fetch_concurrency")
            ),
            "source_fetch_concurrency_adaptive": (
                tenant.source_fetch_concurrency_adaptive
                if tenant.source_fetch_concurrency_adaptive is not None
                else base.get("source_fetch_concurrency_adaptive")
            ),
            "source_preparation_concurrency": (
                tenant.source_preparation_concurrency
                if tenant.source_preparation_concurrency is not None
                else base.get("source_preparation_concurrency")
            ),
            "source_preparation_concurrency_adaptive": (
                tenant.source_preparation_concurrency_adaptive
                if tenant.source_preparation_concurrency_adaptive is not None
                else base.get("source_preparation_concurrency_adaptive")
            ),
            "pipeline_item_concurrency": (
                tenant.pipeline_item_concurrency
                if tenant.pipeline_item_concurrency is not None
                else base.get("pipeline_item_concurrency")
            ),
            "pipeline_item_concurrency_adaptive": (
                tenant.pipeline_item_concurrency_adaptive
                if tenant.pipeline_item_concurrency_adaptive is not None
                else base.get("pipeline_item_concurrency_adaptive")
            ),
            "pipeline_max_text_length": tenant.pipeline_max_text_length,
            "filter_profile_path": tenant.filter_profile_path,
            "schedule_interval_seconds": (
                tenant.schedule.interval_seconds if tenant.schedule is not None else None
            ),
            "output_path": tenant.output.render_path(tenant_id),
            "output_jsonl": tenant.output.jsonl,
            "output_schema_version": tenant.output.schema_version,
            "quarantine_output_path": tenant.quarantine_output.render_path(tenant_id),
            "quarantine_output_jsonl": tenant.quarantine_output.jsonl,
            "quarantine_output_schema_version": tenant.quarantine_output.schema_version,
            "review_output_path": tenant.review_output.render_path(tenant_id),
            "review_output_jsonl": tenant.review_output.jsonl,
            "review_output_schema_version": tenant.review_output.schema_version,
            "review_output_backend": (
                tenant.review_output.backend
                if tenant.review_output.backend is not None
                else base.get("review_output_backend")
            ),
            "rejected_output_path": tenant.rejected_output.render_path(tenant_id),
            "rejected_output_jsonl": tenant.rejected_output.jsonl,
            "rejected_output_schema_version": tenant.rejected_output.schema_version,
            "rejected_output_backend": (
                tenant.rejected_output.backend
                if tenant.rejected_output.backend is not None
                else base.get("rejected_output_backend")
            ),
            "review_max_quality_score": tenant.review_max_quality_score,
            "posting_min_quality_score": tenant.posting_min_quality_score,
            "store_path": Path(str(tenant.store_path).format(tenant_id=tenant_id)),
            "job_store_path": (
                Path(str(tenant.job_store_path).format(tenant_id=tenant_id))
                if tenant.job_store_path is not None
                else None
            ),
            "store_dsn": base.get("store_dsn"),
            "store_pool_min": tenant.store_pool_min,
            "store_pool_max": tenant.store_pool_max,
            "store_fallback_on_error": tenant.store_fallback_on_error,
            "memory_max_keys": tenant.memory_max_keys,
            "memory_max_set_members": tenant.memory_max_set_members,
            "job_backend": tenant.job_backend,
            "search_backend": (
                tenant.search_backend
                if tenant.search_backend is not None
                else base.get("search_backend")
            ),
            "vector_backend": (
                tenant.vector_backend
                if tenant.vector_backend is not None
                else base.get("vector_backend")
            ),
            "language_detection_enabled": tenant.language_detection_enabled,
            "search_language": tenant.search_language,
        }
    )
    base.update(tenant_model_overrides(tenant))
    return Settings.model_validate(base)


def configure(path: str | Path) -> PipelineBuilder:
    from uuid import uuid4

    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

    config_path = Path(path)
    tenant = load_tenant_config(config_path)
    settings = tenant_to_settings(tenant)
    auth = EnvAuthProvider()
    store = cast(Store, create_store(settings))
    job_group_store = cast(JobGroupStore, create_job_group_store(settings))
    llm = cast("LLMProvider", create_llm(settings))
    catalog = load_profile_catalog(settings)
    run_id = uuid4().hex
    sanitize_node, _snapshot_filter, nodes = build_nodes(
        settings,
        store,
        llm,
        job_group_store,
        catalog=catalog,
        run_id=run_id,
        tenant_id=settings.tenant_id or "default",
    )
    output_sink, main_sink, review_sink, posting_sink = build_output_sinks(settings, store=store)
    rejected_counted, rejected_sink = build_rejected_sink(settings, store=store)

    builder = PipelineBuilder(settings=settings)
    builder.sources(tenant.sources)
    builder.auth(auth)
    builder.store(store)
    builder.with_snapshot_filter(
        run_id=run_id, tenant_id=settings.tenant_id or "default", snapshot_filter=_snapshot_filter
    )
    builder.stage(sanitize_node)
    for node in nodes:
        builder.stage(node)
    builder.sink(output_sink)
    builder.with_delivery_targets(build_delivery_targets(settings, posting_sink))
    builder.with_quarantine_sink(build_quarantine_sink(settings))
    builder.with_rejected_sink(rejected_sink, counted=rejected_counted)
    builder.set_source_fetch_concurrency(
        resolve_settings_source_fetch_concurrency(
            settings,
            source_specs=tenant.sources,
        )
    )
    builder.set_pipeline_item_concurrency(
        resolve_settings_pipeline_item_concurrency(
            settings,
            source_specs=tenant.sources,
            source_count=len(tenant.sources),
        )
    )
    builder.set_default_max_items(settings.pipeline_max_items_per_run)
    builder.set_summary_context(
        output_sink=main_sink,
        review_sink=review_sink,
        posting_sink=posting_sink,
        job_group_store=job_group_store,
        profile_name=catalog.catalog_name,
        output_path=settings.output_path,
    )
    if tenant.schedule and tenant.schedule.interval_seconds is not None:
        builder.schedule(tenant.schedule.interval_seconds)
    return builder


def run(path: str | Path) -> RunSummary:
    return asyncio.run(configure(path).run_async())


def build_composite_source_from_file(
    path: Path,
    store: Any = None,
    *,
    concurrency: int = 1,
    dynamic_enabled: bool = False,
    soft_deadline_seconds: float | None = None,
    hard_deadline_seconds: float | None = None,
    overflow_concurrency: int | None = None,
    hard_cancel_grace_seconds: float | None = None,
    adaptive_resize: bool = False,
    concurrency_max: int | None = None,
) -> Source[RawItem]:
    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
    from job_ftch.infrastructure.sources.composite import CompositeSource

    auth = EnvAuthProvider()
    specs = load_sources(path)
    child_sources = [create_source_from_spec(spec, auth, store=store) for spec in specs]
    return CompositeSource(
        cast("Sequence[Source[RawItem]]", child_sources),
        concurrency=concurrency,
        dynamic_enabled=dynamic_enabled,
        soft_deadline_seconds=soft_deadline_seconds,
        hard_deadline_seconds=hard_deadline_seconds,
        overflow_concurrency=overflow_concurrency,
        hard_cancel_grace_seconds=hard_cancel_grace_seconds,
        adaptive_resize=adaptive_resize,
        concurrency_max=concurrency_max,
    )


def build_source(settings: Settings, store: Any = None) -> Source[RawItem]:
    if settings.sources_file_path:
        specs = load_sources(settings.sources_file_path)
        return build_composite_source_from_file(
            settings.sources_file_path,
            store=store,
            concurrency=resolve_settings_source_fetch_concurrency(
                settings,
                source_specs=specs,
            ),
            dynamic_enabled=settings.source_pool_dynamic_enabled,
            soft_deadline_seconds=settings.source_soft_deadline_seconds,
            hard_deadline_seconds=settings.source_hard_deadline_seconds,
            overflow_concurrency=settings.source_overflow_concurrency,
            hard_cancel_grace_seconds=settings.source_hard_cancel_grace_seconds,
            adaptive_resize=settings.source_pool_adaptive_resize,
            concurrency_max=settings.source_fetch_concurrency_max,
        )
    return cast(Source[RawItem], create_source(settings))


def resolve_settings_source_specs(settings: Settings) -> list[SourceSpec]:
    if settings.sources_file_path:
        return load_sources(settings.sources_file_path)
    return []


def resolve_settings_source_count(settings: Settings) -> int:
    source_specs = resolve_settings_source_specs(settings)
    return max(1, len(source_specs)) if source_specs else 1


def resolve_settings_source_fetch_concurrency(
    settings: Settings,
    *,
    source_specs: Sequence[SourceSpec] | None = None,
    cpu_count: int | None = None,
) -> int:
    specs = list(source_specs or resolve_settings_source_specs(settings))
    source_count = max(1, len(specs)) if specs else 1
    source_work_units = estimate_source_work_units(specs)
    plan = resolve_source_fetch_concurrency(
        requested=settings.source_fetch_concurrency,
        source_count=source_count,
        source_work_units=source_work_units,
        adaptive=settings.source_fetch_concurrency_adaptive,
        cpu_count=cpu_count,
    )
    structlog.get_logger("job_ftch.builder").info(
        "source_fetch_concurrency_resolved",
        requested=plan.requested,
        effective=plan.effective,
        adaptive=plan.adaptive,
        source_count=plan.source_count,
        source_work_units=plan.source_work_units,
        cpu_count=plan.cpu_count,
        source_cap=plan.source_cap,
        cpu_cap=plan.cpu_cap,
    )
    return plan.effective


def resolve_settings_source_preparation_concurrency(
    settings: Settings,
    *,
    source_specs: Sequence[SourceSpec] | None = None,
    cpu_count: int | None = None,
) -> int:
    specs = list(source_specs or resolve_settings_source_specs(settings))
    source_count = max(1, len(specs)) if specs else 1
    source_work_units = estimate_source_work_units(specs)
    plan = resolve_source_preparation_concurrency(
        requested=settings.source_preparation_concurrency,
        source_count=source_count,
        source_work_units=source_work_units,
        adaptive=settings.source_preparation_concurrency_adaptive,
        cpu_count=cpu_count,
    )
    structlog.get_logger("job_ftch.builder").info(
        "source_preparation_concurrency_resolved",
        requested=plan.requested,
        effective=plan.effective,
        adaptive=plan.adaptive,
        source_count=plan.source_count,
        source_work_units=plan.source_work_units,
        cpu_count=plan.cpu_count,
        source_cap=plan.source_cap,
        cpu_cap=plan.cpu_cap,
    )
    return plan.effective


def resolve_settings_pipeline_item_concurrency(
    settings: Settings,
    *,
    source_specs: Sequence[SourceSpec] | None = None,
    source_count: int,
    cpu_count: int | None = None,
) -> int:
    specs = list(source_specs or resolve_settings_source_specs(settings))
    source_work_units = estimate_source_work_units(specs)
    plan = resolve_pipeline_item_concurrency(
        requested=settings.pipeline_item_concurrency,
        source_count=source_count,
        source_work_units=source_work_units,
        store_pool_max=settings.store_pool_max,
        adaptive=settings.pipeline_item_concurrency_adaptive,
        cpu_count=cpu_count,
    )
    structlog.get_logger("job_ftch.builder").info(
        "pipeline_item_concurrency_resolved",
        requested=plan.requested,
        effective=plan.effective,
        adaptive=plan.adaptive,
        source_count=plan.source_count,
        source_work_units=plan.source_work_units,
        cpu_count=plan.cpu_count,
        source_cap=plan.source_cap,
        cpu_cap=plan.cpu_cap,
        backend_cap=plan.backend_cap,
    )
    return plan.effective


def build_classifier(_settings: Settings, _catalog: ProfileCatalog) -> ClassifierProvider | None:
    try:
        from job_ftch.infrastructure.classifiers.keyword_classifier import KeywordClassifierProvider
    except Exception:
        return None
    return KeywordClassifierProvider()


def load_profile_catalog(settings: Settings) -> ProfileCatalog:
    if settings.filter_profile_path is None:
        return ProfileCatalog(catalog_name="empty", profiles=())
    return load_catalog_from_path(settings.filter_profile_path)


def merge_derived_ontology(catalog: ProfileCatalog, derived: dict[str, Any]) -> ProfileCatalog:
    pos_keywords = [item["term"] for item in derived.get("positive_keywords", []) if "term" in item]
    neg_keywords = [item["term"] for item in derived.get("negative_keywords", []) if "term" in item]
    anti_patterns = derived.get("anti_patterns", [])
    skills = derived.get("skills", [])
    negative_roles = derived.get("negative_roles", [])
    negative_skills = derived.get("negative_skills", [])
    pos_examples = derived.get("positive_examples", [])
    neg_examples = derived.get("negative_examples", [])
    profile_description = derived.get("profile_description", "")

    def _merge(existing: Any, new_items: Any) -> list[str]:
        seen = {x.casefold() for x in existing}
        res = list(existing)
        for x in new_items:
            if x.casefold() not in seen:
                seen.add(x.casefold())
                res.append(x)
        return tuple(res)  # type: ignore

    updated_profiles = []
    for profile in catalog.profiles:
        update_kwargs: dict[str, Any] = {}

        # Derived roles describe ontology vocabulary, not the user's hiring
        # contract. Expanding target_roles here makes broad historical roles
        # such as "developer" or "product manager" indistinguishable from the
        # explicit profile boundary and inflates false positives.

        if profile_description and hasattr(profile, "profile_description"):
            update_kwargs["profile_description"] = profile_description

        if pos_keywords:
            if hasattr(profile, "positive_relevance_keywords"):
                update_kwargs["positive_relevance_keywords"] = _merge(
                    profile.positive_relevance_keywords, pos_keywords
                )
            elif hasattr(profile, "soft_preferences"):
                update_kwargs["soft_preferences"] = _merge(profile.soft_preferences, pos_keywords)

        if neg_keywords or anti_patterns or negative_roles or negative_skills:
            combined_neg = neg_keywords + anti_patterns + negative_roles + negative_skills
            if hasattr(profile, "negative_relevance_keywords"):
                update_kwargs["negative_relevance_keywords"] = _merge(
                    profile.negative_relevance_keywords, combined_neg
                )
                if hasattr(profile, "anti_preferences"):
                    update_kwargs["anti_preferences"] = _merge(
                        profile.anti_preferences, combined_neg
                    )
            elif hasattr(profile, "anti_preferences"):
                update_kwargs["anti_preferences"] = _merge(profile.anti_preferences, combined_neg)

        if skills and hasattr(profile, "preferred_skills"):
            existing = profile.preferred_skills
            # Normalise existing entries to plain strings so we can merge
            # them with the static ``skills`` list. The merged payload must
            # go back into SearchProfile as SkillTag objects because
            # MultiProfileMatchNode reads ``canonical_name`` directly.
            if existing and isinstance(existing[0], SkillTag):
                existing_strs = tuple(t.canonical_name for t in existing)
            elif existing and isinstance(existing[0], str):
                existing_strs = tuple(existing)
            else:
                existing_strs = ()
            merged = _merge(
                existing_strs,
                [s for s in skills if isinstance(s, str) and s.strip()],
            )
            update_kwargs["preferred_skills"] = tuple(
                SkillTag(canonical_name=s, source="derived_shots") for s in merged
            )

        # Examples keep current behaviour (append/merge)
        if hasattr(profile, "positive_example_texts"):
            update_kwargs["positive_example_texts"] = _merge(
                profile.positive_example_texts, pos_examples
            )

        if hasattr(profile, "negative_example_texts"):
            update_kwargs["negative_example_texts"] = _merge(
                profile.negative_example_texts, neg_examples
            )

        updated_profiles.append(profile.model_copy(update=update_kwargs))

    return catalog.model_copy(update={"profiles": tuple(updated_profiles)})


def _merge_ontology_dicts(seed: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Merge runtime ontology on top of the static seed.

    The seed is a baseline so a brand-new tenant has *some* skill
    catalogue before the user has added a single shot. The runtime
    ontology is what the LLM extracted from the user's actual shots;
    it takes priority for two reasons:

    1. Recency: the user's data is more recent than the seed.
    2. Specificity: the seed has only generic roles like
       "AI Engineer" while the user added "Senior LLM Engineer"
       or "AI Automation Specialist". A merge with the runtime
       layer on top preserves both.

    The merge is a per-field list concat followed by a case-fnew
    de-dupe, so a role that appears in both the seed and the
    runtime only shows up once.
    """
    out: dict[str, Any] = {}
    keys = (
        "positive_keywords",
        "negative_keywords",
        "anti_patterns",
        "skills",
        "roles",
        "negative_roles",
        "negative_skills",
    )
    for k in keys:
        seed_items = list(seed.get(k) or ())
        runtime_items = list(runtime.get(k) or ())
        seen: set[str] = set()
        merged: list[Any] = []
        for item in seed_items + runtime_items:
            if not isinstance(item, str):
                merged.append(item)
                continue
            key = item.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
        out[k] = merged
    for keyword_bucket in ("positive_keywords", "negative_keywords"):
        if keyword_bucket in out and out[keyword_bucket]:
            first = out[keyword_bucket][0]
            if isinstance(first, dict):
                seen_terms: set[str] = set()
                deduped: list[Any] = []
                for item in out[keyword_bucket]:
                    if not isinstance(item, dict):
                        deduped.append(item)
                        continue
                    term = str(item.get("term", "")).casefold().strip()
                    if term and term not in seen_terms:
                        seen_terms.add(term)
                        deduped.append(item)
                out[keyword_bucket] = deduped
    return out


def _normalize_runtime_keyword_items(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            payload = item.model_dump()
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            continue
        term = str(payload.get("term", "")).strip()
        if not term:
            continue
        weight = int(payload.get("weight", 1))
        normalized.append({"term": term, "weight": weight})
    return normalized


async def _build_runtime_ontology_payload(ontology_store: object | None) -> dict[str, Any]:
    """Read the live ontology tables into the same dict shape as
    ``fixtures/shots/derived_ontology.json``.

    Returns an empty dict when ``ontology_store`` is ``None`` (e.g.
    legacy / file backend) so the caller can safely merge the seed
    with the runtime.
    """
    if ontology_store is None:
        return {}
    payload: dict[str, list[Any]] = {
        "positive_keywords": [],
        "negative_keywords": [],
        "anti_patterns": [],
        "skills": [],
        "roles": [],
        "negative_roles": [],
        "negative_skills": [],
    }
    # list_skills / list_roles are async on both
    # DBOntologyStore and PostgresOntologyStore.
    list_skills = getattr(ontology_store, "list_skills", None)
    list_roles = getattr(ontology_store, "list_roles", None)
    list_seniority = getattr(ontology_store, "list_seniority", None)
    list_anti = getattr(ontology_store, "list_anti_patterns", None)
    list_pos_keywords = getattr(ontology_store, "list_positive_keywords", None)
    list_neg_keywords = getattr(ontology_store, "list_negative_keywords", None)
    list_negative_skills = getattr(ontology_store, "list_negative_skills", None)
    list_negative_roles = getattr(ontology_store, "list_negative_roles", None)
    if callable(list_skills):
        with suppress(Exception):
            payload["skills"] = list(await list_skills())
    if callable(list_roles):
        with suppress(Exception):
            payload["roles"] = list(await list_roles())
    if callable(list_anti):
        with suppress(Exception):
            payload["anti_patterns"] = list(await list_anti())
    if callable(list_negative_skills):
        with suppress(Exception):
            payload["negative_skills"] = list(await list_negative_skills())
    if callable(list_negative_roles):
        with suppress(Exception):
            payload["negative_roles"] = list(await list_negative_roles())
    if callable(list_pos_keywords):
        with suppress(Exception):
            payload["positive_keywords"] = _normalize_runtime_keyword_items(
                list(await list_pos_keywords())
            )
    if callable(list_neg_keywords):
        with suppress(Exception):
            payload["negative_keywords"] = _normalize_runtime_keyword_items(
                list(await list_neg_keywords())
            )
    # Seniority and negative_roles are not currently surfaced to
    # the pipeline as keyword lists; keep them on the payload for
    # future use so a debug log can already see them.
    if callable(list_seniority):
        with suppress(Exception):
            payload["seniority"] = list(await list_seniority())
    return payload


def build_v2_typed_bindings(
    *,
    nodes: Sequence[Stage[Any, Any]],
    store: Store,
    catalog: ProfileCatalog,
    relevance_llm: LLMProvider,
    low_threshold: float,
    high_threshold: float,
    max_per_run: int,
    relevance_prompts: dict[str, str | None] | None,
    tenant_id: str,
    graph_hash: str,
    post_accept_llm: LLMProvider | None = None,
    post_accept_group_store: JobGroupStore | None = None,
    post_accept_max_calls: int | None = None,
    post_accept_target_roles: tuple[str, ...] = (),
    capture_payloads: bool = False,
) -> dict[str, Any]:
    """Expose the configured legacy bridge as individual schema-v2 stages.

    This stays in the composition root: graph runtime only binds injected
    stages and never imports node implementations itself.
    """
    bridge = next((node for node in nodes if isinstance(node, EvidenceDecisionNode)), None)
    if bridge is None:
        raise RuntimeError("v2 graph requires the configured evidence-decision bridge")
    fanout = getattr(bridge, "_fanout", None)
    decision = getattr(bridge, "_decision", None)
    if fanout is None or decision is None:
        raise RuntimeError("configured evidence-decision bridge lacks typed v2 stages")
    post_accept_stages: tuple[Stage[Any, Any], ...] = ()
    if post_accept_llm is not None:
        post_accept_stages = (
            FullExtractionNode(
                post_accept_llm,
                max_calls=post_accept_max_calls,
                target_roles=post_accept_target_roles,
                capture_payloads=capture_payloads,
            ),
            AcceptTemplatePresentationNode(),
        )
    return {
        "evidence_fanout": fanout,
        "decision": decision,
        "llm_relevance_evidence": LLMRelevanceEvidenceNode(
            llm=relevance_llm,
            store=store,
            catalog=catalog,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
            max_per_run=max_per_run,
            relevance_prompts=relevance_prompts,
            tenant_id=tenant_id,
            graph_hash=graph_hash,
        ),
        "post_accept_enrichment": PostAcceptEnrichment(
            stages=post_accept_stages,
            group_store=post_accept_group_store,
        ),
    }


def build_nodes(
    settings: Settings,
    store: Store,
    llm: LLMProvider,
    job_group_store: JobGroupStore,
    catalog: ProfileCatalog,
    *,
    run_id: str | None = None,
    tenant_id: str = "default",
    user_id: str | None = None,
    ontology_store: object | None = None,
    relevance_prompts: dict[str, str | None] | None = None,
    derived_ontology_override: dict[str, Any] | None = None,
    bgem3_provider: BgeMThreeProviderPort | None = None,
) -> tuple[SanitizingNode[RawItem], SnapshotFilterNode | None, Sequence[Stage[Any, Any]]]:
    """Return (SanitizeNode, optional SnapshotFilterNode, processing nodes).

    The SnapshotFilterNode is included as the first element of `nodes`
    (i.e. the 2nd stage overall, after SanitizeNode) when `run_id` is set.
    Pass `run_id=None` (the default) to skip the snapshot filter entirely —
    useful for ephemeral / one-shot runs that do not need cross-run dedup.
    The Pipeline orchestrator calls `save_and_purge()` on the returned
    filter at the end of `run()`.
    """
    _log_relevance_window(settings)
    # Ontology merge order — the runtime ontology (jf_ontology_*
    # tables populated by the LLM when the user adds a shot) takes
    # priority over the static fixtures seed, and the user's own
    # SearchProfile data (already in the catalog from
    # ``_build_runtime_catalog``) is never overwritten. The seed
    # is a fallback so a brand-new tenant still has *some* skill
    # baseline before the user has added a single shot.
    # ``derived_ontology_override`` (if passed) is used as-is and
    # the on-disk seed is skipped. The pipeline runner uses this
    # to inject the runtime ontology it just loaded from Postgres.
    if derived_ontology_override is not None:
        runtime_derived = derived_ontology_override
        seed_derived: dict[str, Any] = {}
    else:
        try:
            derived_path = Path(
                getattr(settings, "derived_ontology_path", "fixtures/shots/derived_ontology.json")
            )
            if derived_path.exists():
                with derived_path.open("r", encoding="utf-8") as f:
                    seed_derived = json.load(f)
            else:
                seed_derived = {}
        except Exception as exc:  # noqa: BLE001
            structlog.get_logger("job_ftch.builder").warning(
                "derived_ontology_seed_load_failed",
                error=str(exc),
            )
            seed_derived = {}
        runtime_derived = {}

    # Runtime takes priority: its keywords/roles are merged AFTER
    # the seed so a user-added role like "Senior LLM Engineer"
    # survives even when the seed only knows "AI Engineer".
    derived = _merge_ontology_dicts(seed_derived, runtime_derived)
    if (
        derived.get("positive_keywords")
        or derived.get("skills")
        or derived.get("roles")
        or runtime_derived
    ):
        catalog = merge_derived_ontology(catalog, derived)

        pos_keywords = [
            item["term"] for item in derived.get("positive_keywords", []) if "term" in item
        ]
        neg_keywords = [
            item["term"] for item in derived.get("negative_keywords", []) if "term" in item
        ]
        anti_patterns = derived.get("anti_patterns", [])
        pos_examples = derived.get("positive_examples", [])
        neg_examples = derived.get("negative_examples", [])

        structlog.get_logger("job_ftch.builder").info(
            "derived_ontology_merged",
            pos_added=len(pos_keywords),
            neg_added=len(neg_keywords),
            anti_added=len(anti_patterns),
            pos_ex_added=len(pos_examples),
            neg_ex_added=len(neg_examples),
            runtime_skills=len(runtime_derived.get("skills", [])),
            runtime_roles=len(runtime_derived.get("roles", [])),
            runtime_anti=len(runtime_derived.get("anti_patterns", [])),
        )

    classifier = build_classifier(settings, catalog)
    from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer

    normalizer = get_default_normalizer()
    snapshot_filter: SnapshotFilterNode | None = None
    if run_id is not None:
        snapshot_filter = SnapshotFilterNode(
            store,
            tenant_id=tenant_id,
            run_id=run_id,
            fail_open=settings.snapshot_fail_open,
        )
    nodes: list[Stage[Any, Any]] = []
    full_extraction_budget = (
        AsyncCallBudget(settings.pipeline_full_extraction_max_calls_per_run)
        if settings.pipeline_full_extraction_max_calls_per_run is not None
        else None
    )
    if snapshot_filter is not None:
        nodes.append(snapshot_filter)
    from job_ftch.infrastructure.classifiers.keyword_lists import (
        load_announcement_tokens,
        load_candidate_tokens,
        load_job_posting_strong_tokens,
        load_job_posting_tokens,
        load_spam_tokens,
    )

    nodes.append(SourceContextNode())
    ontology_snapshots = {
        profile.profile_id: OntologySnapshot(
            tenant_id=tenant_id,
            profile_id=profile.profile_id,
            payload_json=json.dumps(
                {
                    "ontology": derived,
                    "profile": profile.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        for profile in catalog.profiles
    }
    if not ontology_snapshots:
        ontology_snapshots["default"] = OntologySnapshot(
            tenant_id=tenant_id,
            profile_id="default",
            payload_json=json.dumps(
                {"ontology": derived, "profile": None},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if ontology_snapshots:
        from job_ftch.nodes.ontology_snapshot import OntologySnapshotNode

        nodes.append(OntologySnapshotNode(ontology_snapshots))
    from job_ftch.nodes.candidate_segmentation import CandidateSegmentationNode

    nodes.append(CandidateSegmentationNode())
    nodes.extend(
        [
            GarbageFilterNode(),
            PostTypeClassificationNode(
                classifier=classifier,
                announcement_tokens=load_announcement_tokens(),
                job_posting_tokens=load_job_posting_tokens(),
                job_posting_strong_tokens=load_job_posting_strong_tokens(),
                candidate_tokens=load_candidate_tokens(),
                spam_tokens=load_spam_tokens(),
            ),
            HardFilterNode(catalog),
            DedupNode(store, defer_commit=True),
        ]
    )

    _bgem3_provider: BgeMThreeProviderPort | None = None
    _bgem3_pos_dense = None
    _bgem3_neg_dense = None
    _bgem3_pos_sparse: list[Any] = []
    _bgem3_neg_sparse: list[Any] = []
    if getattr(settings, "bgem3_enabled", False):
        try:
            from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider
            from job_ftch.nodes.bge_embed_node import BgeMThreeNode

            _bgem3_provider = bgem3_provider or BgeMThreeProvider(settings.bgem3_model)
            nodes.append(BgeMThreeNode(_bgem3_provider, max_length=1024))
            structlog.get_logger("job_ftch.builder").info(
                "bgem3_node_enabled",
                model=settings.bgem3_model,
            )
        except ImportError:
            structlog.get_logger("job_ftch.builder").warning(
                "bgem3_skipped",
                reason="FlagEmbedding not installed",
            )
            _bgem3_provider = None
    elif settings.embedding_prefilter_enabled:
        try:
            from job_ftch.infrastructure.embeddings.sentence_transformers_provider import (
                LocalSentenceTransformersProvider,
            )
            from job_ftch.nodes.embedding_prefilter import EmbeddingPrefilterNode

            _prefilter_provider = LocalSentenceTransformersProvider(
                settings.embedding_prefilter_model
            )
            nodes.append(EmbeddingPrefilterNode(catalog, _prefilter_provider))
            structlog.get_logger("job_ftch.builder").info(
                "embedding_prefilter_enabled",
                model=settings.embedding_prefilter_model,
            )
        except ImportError:
            structlog.get_logger("job_ftch.builder").warning(
                "embedding_prefilter_skipped",
                reason="sentence-transformers not installed",
            )

    # Semantic prefilter runs AFTER embedding so it can honour the cross-lingual signal
    # (OR-logic: keep if EITHER token-overlap OR embedding role-anchor is strong).
    _relevance_scorer: Any = None
    _catalog_has_shots = any(
        profile.positive_example_texts
        or profile.negative_example_texts
        or profile.positive_job_example_texts
        or profile.negative_job_example_texts
        for profile in catalog.profiles
    )
    if getattr(settings, "relevance_backend", "keywords") == "shots" and _catalog_has_shots:
        try:
            import os as _os

            # BGE-M3 path: reads pre-computed dense vectors from metadata (no re-encoding).
            if _bgem3_provider is not None:
                import numpy as np

                from job_ftch.infrastructure.relevance import shot_registry
                from job_ftch.infrastructure.relevance.shot_anchor import (
                    BgeMThreeShotScorer,
                )

                _backend = getattr(settings, "relevance_shot_backend", "memory")
                _pos: np.ndarray
                _neg: np.ndarray
                _pos_sparse: list[Any]
                _neg_sparse: list[Any]
                if _backend == "memory":
                    _registry_store = shot_registry.get_store()
                    if _registry_store is None:
                        # Fallback: read from the catalog in front of us.
                        # The bot normally configures the registry at
                        # startup, but in CLI / eval contexts the catalog
                        # is the only source of truth.
                        from job_ftch.infrastructure.relevance.shot_anchor import (
                            InMemoryBgeMThreeShotStore,
                        )

                        _registry_store = InMemoryBgeMThreeShotStore(provider=_bgem3_provider)
                        _registry_store.replace_from_profiles(p for p in catalog.profiles)
                    _pos, _neg, _pos_sparse, _neg_sparse = _registry_store.load(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        categories=("vacancy",),
                    )
                elif _backend == "qdrant":
                    from job_ftch.infrastructure.relevance.shot_anchor import (
                        BgeMThreeQdrantShotStore,
                    )

                    _load_plans = _resolve_shot_load_plans(
                        settings,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    _pos_parts: list[Any] = []
                    _neg_parts: list[Any] = []
                    _pos_sparse = []
                    _neg_sparse = []
                    for _plan in _load_plans:
                        _log_active_shot_backend(
                            backend=_backend,
                            collection=_plan.collection,
                            source_mode=getattr(
                                settings, "relevance_shot_source_mode", "user_db_only"
                            ),
                            tenant_id=_plan.tenant_id,
                            user_id=_plan.user_id,
                        )
                        _qdrant_store = BgeMThreeQdrantShotStore(
                            url=str(settings.qdrant_url),
                            api_key=(
                                settings.qdrant_api_key.get_secret_value()
                                if settings.qdrant_api_key is not None
                                else _os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
                            ),
                            collection=_plan.collection,
                            provider=_bgem3_provider,
                        )
                        _qdrant_store.ensure_collection()
                        _loaded_pos, _loaded_neg, _loaded_pos_sparse, _loaded_neg_sparse = (
                            _qdrant_store.load(
                                role_prefix=_plan.role_prefix,
                                tenant_id=_plan.tenant_id,
                                user_id=_plan.user_id,
                                categories=("vacancy",),
                            )
                        )
                        if _loaded_pos.size:
                            _pos_parts.append(_loaded_pos)
                            _pos_sparse.extend(_loaded_pos_sparse)
                        if _loaded_neg.size:
                            _neg_parts.append(_loaded_neg)
                            _neg_sparse.extend(_loaded_neg_sparse)
                    _pos = (
                        np.vstack(_pos_parts)
                        if _pos_parts
                        else np.zeros((0, _bgem3_provider.dim), dtype=np.float32)
                    )
                    _neg = (
                        np.vstack(_neg_parts)
                        if _neg_parts
                        else np.zeros((0, _bgem3_provider.dim), dtype=np.float32)
                    )
                else:
                    raise ValueError(
                        f"relevance_shot_backend must be 'memory' or 'qdrant', got {_backend!r}"
                    )

                if _pos.size or _neg.size:
                    _relevance_scorer = BgeMThreeShotScorer(
                        _pos, _neg, pos_sparse=_pos_sparse, neg_sparse=_neg_sparse
                    )
                    # Store for ParallelScoringNode wired later
                    _bgem3_pos_dense = _pos
                    _bgem3_neg_dense = _neg
                    _bgem3_pos_sparse = _pos_sparse
                    _bgem3_neg_sparse = _neg_sparse
                    structlog.get_logger("job_ftch.builder").info(
                        "relevance_backend_bgem3_shots",
                        backend=_backend,
                        positives=int(_pos.shape[0]),
                        negatives=int(_neg.shape[0]),
                    )
                else:
                    structlog.get_logger("job_ftch.builder").warning(
                        "relevance_bgem3_shots_empty_fallback_keywords",
                        backend=_backend,
                    )
                    _bgem3_pos_dense = _bgem3_neg_dense = None
                    _bgem3_pos_sparse = []
                    _bgem3_neg_sparse = []
            else:
                # Legacy e5-small path.
                from job_ftch.infrastructure.relevance.shot_anchor import (
                    ShotEmbedder,
                    ShotRelevanceScorer,
                    ShotStore,
                )

                _embedder = ShotEmbedder(settings.relevance_shot_model)
                _store = ShotStore(
                    url=str(settings.qdrant_url),
                    api_key=(
                        settings.qdrant_api_key.get_secret_value()
                        if settings.qdrant_api_key is not None
                        else _os.environ.get("JOB_FTCH_QDRANT_API_KEY") or None
                    ),
                    collection=settings.relevance_shot_collection,
                )
                _pos, _neg = _store.load()
                if _pos.size and _neg.size:
                    _relevance_scorer = ShotRelevanceScorer(_pos, _neg, _embedder)
                    structlog.get_logger("job_ftch.builder").info(
                        "relevance_backend_shots",
                        positives=int(_pos.shape[0]),
                        negatives=int(_neg.shape[0]),
                    )
                else:
                    structlog.get_logger("job_ftch.builder").warning(
                        "relevance_shots_empty_fallback_keywords"
                    )
        except Exception as exc:  # noqa: BLE001 - DB/model optional at runtime
            structlog.get_logger("job_ftch.builder").warning(
                "relevance_shots_unavailable_fallback_keywords", error=str(exc)
            )
    elif False:  # shot scoring is post-accept enrichment
        structlog.get_logger("job_ftch.builder").warning(
            "relevance_shots_skipped_empty_profile",
            reason="no_profile_shots",
        )

    nodes.append(
        TfidfLogregRelevancePrefilterNode(
            threshold=0.35,
            mode="gate",
            model_path="fixtures/prefilter/tfidf_logreg_v1.json",
        )
    )
    nodes.append(
        SemanticPrefilterNode(
            catalog,
            relevance_scorer=_relevance_scorer,
            relevance_threshold=getattr(settings, "relevance_shot_threshold", 0.0),
        )
    )
    if settings.pipeline_jobness_decision_enabled:
        from job_ftch.nodes.jobness import RawJobnessEvidenceNode

        nodes.append(RawJobnessEvidenceNode())

    _seen_roles: set[str] = set()
    _target_roles: list[str] = []
    for _profile in catalog.profiles:
        for _role in _profile.target_roles:
            _key = _role.casefold().strip()
            if _key and _key not in _seen_roles:
                _seen_roles.add(_key)
                _target_roles.append(_role)
    _target_roles_tuple = tuple(_target_roles[:60])  # cap prompt size

    if settings.pipeline_completeness_gate_enabled:
        nodes.append(CompletenessGateNode(threshold=settings.pipeline_completeness_threshold))
    nodes.extend(
        [
            ExtractionNode(
                llm,
                max_calls=settings.pipeline_full_extraction_max_calls_per_run,
                budget=full_extraction_budget,
                target_roles=_target_roles_tuple,
                min_search_relevance=settings.extraction_min_search_relevance,
                min_hiring_intent=settings.extraction_min_hiring_intent,
                capture_payloads=settings.tracing_capture_payloads,
                scope="core",
            ),
            ExtractionValidationNode(),
            TitleCompanyNormalizationNode(normalizer),
            SkillNormalizationNode(normalizer),
            LocationWorkModeNormalizationNode(),
            CompensationParsingNode(),
            JobLifecycleNode(),
        ]
    )

    if settings.pipeline_jobness_decision_enabled:
        from job_ftch.nodes.jobness import JobnessEvidenceProducer

        nodes.append(JobnessEvidenceProducer())

    if settings.language_detection_enabled:
        from job_ftch.infrastructure.language.detector import LinguaLanguageDetector
        from job_ftch.nodes.language_detection import LanguageDetectionNode

        nodes.append(LanguageDetectionNode(LinguaLanguageDetector()))

    # Translation is post-accept enrichment. Keeping it out of this graph
    # prevents a language-model/translation failure from changing policy.

    nodes.append(MultiProfileMatchNode(catalog))
    nodes.append(LexicalEvidenceNode(catalog))

    nodes.extend(
        [
            RiskScoringNode(),
            QualityScoringNode(),
            JobValidationNode(
                min_relevance_score=0.05,
                llm_band_floor=settings.llm_relevance_low_threshold
                if settings.llm_relevance_max_per_run > 0
                else None,
                enforce_policy=False,
            ),
        ]
    )

    # Per-run LLM cap: one atomic budget shared into the LLM node so the unit is
    # reserved before the call, not counted after it. Relevance is the only node
    # this run cap applies to (the previous post-hoc guard also counted relevance
    # calls only), so the effective ceiling is min(run cap, per-node cap) and both
    # limits are honoured. Left None when unset, keeping the per-node cap in force.
    run_llm_budget = (
        AsyncCallBudget(
            min(settings.pipeline_max_llm_calls_per_run, settings.llm_relevance_max_per_run)
        )
        if settings.pipeline_max_llm_calls_per_run is not None
        else None
    )
    if settings.pipeline_max_browser_navigations_per_run is not None:
        # Browser navigations happen in the source/fetch layer, not in the item
        # workers, so this run cap is not enforced here yet. Surface it instead of
        # pretending it is a hard ceiling.
        structlog.get_logger("job_ftch.builder").warning(
            "browser_navigation_budget_not_enforced",
            configured=settings.pipeline_max_browser_navigations_per_run,
        )
    llm_relevance_node = LLMRelevanceClassificationNode(
        llm=llm,
        store=store,
        catalog=catalog,
        low_threshold=settings.llm_relevance_low_threshold,
        high_threshold=settings.llm_relevance_high_threshold,
        max_per_run=settings.llm_relevance_max_per_run,
        budget=run_llm_budget,
        relevance_prompts=relevance_prompts,
        tenant_id=tenant_id,
    )
    llm_relevance_node.configure_graph_params({"call_policy": "force_all"})
    nodes.extend(
        [
            llm_relevance_node,
            EvidenceDecisionNode(settings=settings),
            JobAggregationNode(job_group_store, attach_group_id=True),
        ]
    )
    _validate_pipeline_graph(settings, nodes)

    return (
        SanitizeNode(
            max_text_length=settings.pipeline_max_text_length,
        ),
        snapshot_filter,
        nodes,
    )


def build_sink(settings: Settings) -> Sink[JobRecord]:
    return cast(Sink[JobRecord], create_sink(settings))


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    quarantine_settings = settings.quarantine_settings().model_copy(
        update={"output_replace_empty": True}
    )
    return FailureTolerantSink(
        # `quarantine_settings` already points at the quarantine output. Passing
        # `quarantine=True` a second time rebuilds Settings and drops the
        # run-local `output_replace_empty` flag.
        create_sink(quarantine_settings),  # type: ignore[arg-type]
        sink_name="quarantine",
    )


def _build_outcome_lane_sink(
    settings: Settings,
    *,
    lane: str,
    lane_backend: str | None,
    file_settings: Settings,
    store: Store | None,
) -> Sink[Any]:
    """Compose file and/or store sinks for REVIEW/REJECTED (opt-in store)."""
    from job_ftch.sinks.null_sink import NullSink as _Null
    from job_ftch.sinks.outcome_store import StoreOutcomeSink

    file_backend, write_store = resolve_outcome_lane_backend(lane_backend, settings.sink_backend)
    targets: list[Sink[Any]] = []
    if file_backend is not None:
        file_cfg = file_settings.model_copy(
            update={"sink_backend": file_backend, "output_replace_empty": True}
        )
        targets.append(create_sink(file_cfg))  # type: ignore[arg-type]
    if write_store:
        if store is None:
            msg = f"{lane}_output_backend requires a store when backend includes 'store'"
            raise ValueError(msg)
        recorder = store
        if not hasattr(recorder, "record_operational_outcome"):
            msg = (
                f"{lane} store sink needs record_operational_outcome "
                f"(got {type(store).__name__})"
            )
            raise TypeError(msg)
        targets.append(StoreOutcomeSink(recorder, lane=lane))  # type: ignore[arg-type]
    if not targets:
        return _Null()
    if len(targets) == 1:
        return targets[0]
    return FanOutSink(targets)


def build_rejected_sink(
    settings: Settings,
    store: Store | None = None,
) -> tuple[CountedSink[RejectedItem], Sink[RejectedItem]]:
    from job_ftch.sinks.outcome_artifact import CompactRejectedSink

    inner = _build_outcome_lane_sink(
        settings,
        lane="rejected",
        lane_backend=settings.rejected_output_backend,
        file_settings=settings.rejected_settings(),
        store=store,
    )
    counted: CountedSink[RejectedItem] = CountedSink(CompactRejectedSink(inner))
    return counted, FailureTolerantSink(counted, sink_name="rejected")


def build_output_sinks(
    settings: Settings,
    store: Store | None = None,
) -> tuple[
    Sink[JobRecord],
    CountedSink[JobRecord],
    CountedSink[JobRecord],
    CountedSink[JobRecord] | None,
]:
    main_settings = settings.model_copy(update={"output_replace_empty": True})
    main_sink: CountedSink[JobRecord] = CountedSink(build_sink(main_settings))
    from job_ftch.sinks.outcome_artifact import CompactReviewSink

    review_inner = _build_outcome_lane_sink(
        settings,
        lane="review",
        lane_backend=settings.review_output_backend,
        file_settings=settings.review_settings(),
        store=store,
    )
    review_counted: CountedSink[JobRecord] = CountedSink(CompactReviewSink(review_inner))
    posting_sink: CountedSink[JobRecord] | None = None
    accept_targets: list[Sink[JobRecord]] = [main_sink]
    if not settings.dry_run and settings.posting_backend != "none":
        posting_counted: CountedSink[JobRecord] = CountedSink(
            create_sink(settings.posting_settings())  # type: ignore[arg-type]
        )
        posting_sink = posting_counted
    accept_lane: Sink[JobRecord] = FanOutSink(accept_targets)
    lane_sink: Sink[JobRecord] = RoutingSink(
        [
            (_should_output(settings), accept_lane),
            (_needs_review(settings), FailureTolerantSink(review_counted, sink_name="review")),
            (lambda job: job.routing_decision == MatchDecision.REJECT, NullSink()),
        ]
    )
    return lane_sink, main_sink, review_counted, posting_sink


def build_delivery_targets(
    settings: Settings, posting_sink: CountedSink[JobRecord] | None
) -> tuple[DeliveryTarget[JobRecord], ...]:
    if posting_sink is None:
        return ()
    from job_ftch.application.delivery import SinkDeliveryTarget

    destination = settings.telegram_publish_entity or str(settings.posting_settings().output_path)
    fingerprint = sha256(destination.encode()).hexdigest()[:16]
    return (SinkDeliveryTarget(f"posting:{settings.posting_backend}:{fingerprint}", posting_sink),)


async def build_store(settings: Settings) -> Store:
    return cast(Store, await create_store_with_fallback(settings))


def build_llm(settings: Settings) -> LLMProvider:
    return cast(LLMProvider, create_llm(settings))


def _needs_review(settings: Settings) -> Callable[[JobRecord], bool]:
    def predicate(job: JobRecord) -> bool:
        if job.routing_decision == MatchDecision.REVIEW:
            return True
        if job.routing_decision == MatchDecision.ACCEPT:
            return False
        if _is_hard_reject(job):
            return False
        return (
            bool(job.review_reasons)
            or (job.quality_score or 0.0) < settings.review_max_quality_score
        )

    return predicate


def _should_output(settings: Settings) -> Callable[[JobRecord], bool]:
    del settings

    def predicate(job: JobRecord) -> bool:
        return job.routing_decision == MatchDecision.ACCEPT

    return predicate


def _should_post(settings: Settings) -> Callable[[JobRecord], bool]:
    del settings

    def predicate(job: JobRecord) -> bool:
        return job.routing_decision == MatchDecision.ACCEPT

    return predicate


def _is_hard_reject(job: JobRecord) -> bool:
    if job.routing_decision != MatchDecision.REJECT:
        return False
    for reason in job.review_reasons:
        if reason == "no_profile_match":
            return True
        if reason.startswith(("llm_relevance:reject", "profile_reject")):
            return True
    return False


async def run_pipeline_from_settings(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    store = await build_store(settings)
    from job_ftch.application.tenant_store import TenantStore

    if not isinstance(store, TenantStore):
        store = TenantStore(
            settings.tenant_id or "default",
            store,
            processed_item_ttl_hours=settings.processed_item_ttl_hours,
        )
    try:
        job_group_store = cast(JobGroupStore, await create_job_group_store_with_fallback(settings))
        llm = build_llm(settings)
        catalog = load_profile_catalog(settings)
        # Per ADR-031 + ADR-036: a fresh run_id is generated per pipeline run
        # so the SnapshotFilterNode can drop items already seen in the last
        # run. Production multi-tenant callers (TenantRunner) override this
        # to keep the id stable across the scheduler tick.
        from uuid import uuid4

        run_id = uuid4().hex
        sanitize_node, _snapshot_filter, nodes = build_nodes(
            settings,
            store,
            llm,
            job_group_store,
            catalog=catalog,
            run_id=run_id,
            tenant_id=settings.tenant_id or "default",
        )
        output_sink, main_sink, review_sink, posting_sink = build_output_sinks(
            settings, store=store
        )
        rejected_counted, rejected_sink = build_rejected_sink(settings, store=store)
        builder = (
            PipelineBuilder(settings=settings)
            .with_runtime_source(build_source(settings, store=store))
            .store(store)
            .stage(sanitize_node)
            .sink(output_sink)
            .with_delivery_targets(build_delivery_targets(settings, posting_sink))
            .with_quarantine_sink(build_quarantine_sink(settings))
            .with_rejected_sink(rejected_sink, counted=rejected_counted)
            .with_snapshot_filter(
                run_id=run_id,
                tenant_id=settings.tenant_id or "default",
                snapshot_filter=_snapshot_filter,
            )
            .set_source_fetch_concurrency(resolve_settings_source_fetch_concurrency(settings))
            .set_source_pool_runtime(
                dynamic_enabled=settings.source_pool_dynamic_enabled,
                soft_deadline_seconds=settings.source_soft_deadline_seconds,
                hard_deadline_seconds=settings.source_hard_deadline_seconds,
                overflow_concurrency=settings.source_overflow_concurrency,
                hard_cancel_grace_seconds=settings.source_hard_cancel_grace_seconds,
                adaptive_resize=settings.source_pool_adaptive_resize,
                concurrency_max=settings.source_fetch_concurrency_max,
            )
            .set_pipeline_item_concurrency(
                resolve_settings_pipeline_item_concurrency(
                    settings,
                    source_specs=resolve_settings_source_specs(settings),
                    source_count=resolve_settings_source_count(settings),
                )
            )
            .set_default_max_items(settings.pipeline_max_items_per_run)
            .set_summary_context(
                output_sink=main_sink,
                review_sink=review_sink,
                posting_sink=posting_sink,
                job_group_store=job_group_store,
                profile_name=catalog.catalog_name,
                output_path=settings.output_path,
            )
        )
        for node in nodes:
            builder.stage(node)
        summary = await builder.run_async(max_items=settings.pipeline_max_items_per_run)
        if summary.finished_at:
            await store.set_run_state("pipeline.finished_at", summary.finished_at.isoformat())
        await store.set_run_state("pipeline.emitted", str(summary.emitted))
        return summary
    finally:
        close_store = getattr(store, "close", None)
        if callable(close_store):
            await close_store()


async def run_search_from_settings(settings: Settings, args: Any) -> None:
    configure_logging(settings.log_level)
    search_backend = create_search_backend(settings)
    try:
        backend = cast(SearchBackend, search_backend)
        results = await backend.search(args.query, limit=args.limit)
        if args.json:
            print(
                json.dumps(
                    [g.model_dump(mode="json") for g in results], ensure_ascii=False, indent=2
                )
            )
        elif args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                for group in results:
                    handle.write(
                        json.dumps(group.canonical_job.model_dump(mode="json"), ensure_ascii=False)
                        + "\n"
                    )
            print(f"Wrote {len(results)} canonical jobs to {args.output}")
        else:
            print(f"Found {len(results)} job groups:\n")
            for group in results:
                print(
                    f"- {group.canonical_job.title} at {group.canonical_job.company} ({group.source_count} sources)"
                )
                if group.canonical_job.description:
                    preview = group.canonical_job.description[:100].replace("\n", " ")
                    print(f"  {preview}...")
                print()
    finally:
        close_backend = getattr(search_backend, "close", None)
        if callable(close_backend):
            await close_backend()


async def show_status_from_settings(settings: Settings) -> None:
    store = cast(Store, await create_store_with_fallback(settings))
    status = await store.get_run_state("pipeline.status")
    finished_at = await store.get_run_state("pipeline.finished_at")
    emitted = await store.get_run_state("pipeline.emitted")
    if status:
        print(f"Pipeline status: {status}")
        print(f"Last finished at: {finished_at or 'unknown'}")
        print(f"Items emitted: {emitted or 0}")
    else:
        print("No run status found in store.")
    close_store = getattr(store, "close", None)
    if callable(close_store):
        await close_store()
