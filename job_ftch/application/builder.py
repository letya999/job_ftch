"""Programmatic pipeline builder and settings-backed runtime helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.contracts import (
    AuthProvider,
    ClassifierProvider,
    EmbeddingProvider,
    JobGroupStore,
    LLMProvider,
    ProcessingNode,
    SanitizingNode,
    SearchBackend,
    Sink,
    Source,
    Stage,
    Store,
    VectorBackend,
)
from job_ftch.application.filter_profile_loader import (
    load_profile_catalog as load_catalog_from_path,
)
from job_ftch.application.logging import configure_logging
from job_ftch.application.pipeline import Pipeline, RunSummary
from job_ftch.application.registry import (
    create_embedding_provider,
    create_job_group_store,
    create_job_group_store_with_fallback,
    create_llm,
    create_search_backend,
    create_sink,
    create_source,
    create_source_from_spec,
    create_store,
    create_store_with_fallback,
    create_vector_backend,
)
from job_ftch.application.source_loader import load_sources
from job_ftch.application.telemetry import configure_telemetry
from job_ftch.config import Settings, get_settings
from job_ftch.domain import (
    JobRecord,
    MatchDecision,
    ProfileCatalog,
    QuarantinedRawItem,
    RawItem,
    RejectedItem,
    TenantConfig,
)
from job_ftch.domain.source_spec import SourceSpec
from job_ftch.nodes import (
    CompensationParsingNode,
    DedupNode,
    ExtractionNode,
    ExtractionValidationNode,
    JobAggregationNode,
    JobLifecycleNode,
    JobValidationNode,
    LocationWorkModeNormalizationNode,
    QualityScoringNode,
    RoutingNode,
    SanitizeNode,
    SkillNormalizationNode,
    TitleCompanyNormalizationNode,
)
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.language_context import SourceContextNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.risk import RiskScoringNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode
from job_ftch.sinks import CountedSink, FailureTolerantSink, FanOutSink, RoutingSink

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider


class PipelineBuilder:
    """Builds pipelines programmatically for library and adapter use."""

    def __init__(self) -> None:
        self._source_specs: list[SourceSpec] = []
        self._source_instance: Source[RawItem] | None = None
        self._auth_provider: AuthProvider | None = None
        self._stages: list[Stage[Any, Any]] = []
        self._sinks: list[Sink[JobRecord]] = []
        self._store: Store | None = None
        self._quarantine_sink: Sink[QuarantinedRawItem] | None = None
        self._rejected_sink: Sink[RejectedItem] | None = None
        self._schedule_interval_seconds: int | None = None
        self._default_max_items: int | None = None
        self._review_sink: CountedSink[JobRecord] | None = None
        self._posting_sink: CountedSink[JobRecord] | None = None
        self._rejected_counted: CountedSink[RejectedItem] | None = None
        self._job_group_store: JobGroupStore | None = None
        self._profile_name: str | None = None
        self._output_path: Path | None = None

    def clone(self) -> PipelineBuilder:
        cloned = self.__class__()
        cloned._source_specs = list(self._source_specs)
        cloned._source_instance = self._source_instance
        cloned._auth_provider = self._auth_provider
        cloned._stages = list(self._stages)
        cloned._sinks = list(self._sinks)
        cloned._store = self._store
        cloned._quarantine_sink = self._quarantine_sink
        cloned._rejected_sink = self._rejected_sink
        cloned._schedule_interval_seconds = self._schedule_interval_seconds
        cloned._default_max_items = self._default_max_items
        cloned._review_sink = self._review_sink
        cloned._posting_sink = self._posting_sink
        cloned._rejected_counted = self._rejected_counted
        cloned._job_group_store = self._job_group_store
        cloned._profile_name = self._profile_name
        cloned._output_path = self._output_path
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

    def stage(self, node: Stage[Any, Any]) -> PipelineBuilder:
        self._stages.append(node)
        return self

    def sink(self, sink: Sink[JobRecord]) -> PipelineBuilder:
        self._sinks.append(sink)
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

    def set_default_max_items(self, value: int | None) -> PipelineBuilder:
        self._default_max_items = value
        return self

    def set_summary_context(
        self,
        *,
        review_sink: CountedSink[JobRecord] | None = None,
        posting_sink: CountedSink[JobRecord] | None = None,
        job_group_store: JobGroupStore | None = None,
        profile_name: str | None = None,
        output_path: Path | None = None,
    ) -> PipelineBuilder:
        self._review_sink = review_sink
        self._posting_sink = posting_sink
        self._job_group_store = job_group_store
        self._profile_name = profile_name
        self._output_path = output_path
        return self

    @property
    def schedule_interval_seconds(self) -> int | None:
        return self._schedule_interval_seconds

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
        if not isinstance(self._stages[0], SanitizeNode):
            msg = "SanitizeNode must be the first stage in the pipeline."
            raise ValueError(msg)
        if not self._sinks:
            msg = "PipelineBuilder requires at least one sink."
            raise ValueError(msg)

        source = self._source_instance or self._build_source_from_specs()
        sanitize_node = cast(SanitizingNode[RawItem], self._stages[0])
        processing_nodes = list(self._stages[1:])
        return Pipeline(
            source=source,
            sanitize_node=sanitize_node,
            nodes=processing_nodes,
            sink=self._sinks[0] if len(self._sinks) == 1 else FanOutSink(self._sinks),
            store=self.get_store(),
            quarantine_sink=self._quarantine_sink,
            rejected_sink=self._rejected_sink,
        )

    async def run_async(self, *, max_items: int | None = None) -> RunSummary:
        pipeline = self.build()
        summary = await pipeline.run(max_items=max_items or self._default_max_items)
        self._apply_summary_context(summary)
        return summary

    def _apply_summary_context(self, summary: RunSummary) -> None:
        summary.applied_profile = self._profile_name or "default"
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
            summary.review = self._review_sink.emit_count
            for source_kind, count in self._review_sink.by_source_kind.items():
                summary.source_stats(source_kind).review = count
        if self._posting_sink is not None:
            summary.posted = self._posting_sink.emit_count
            for source_kind, count in self._posting_sink.by_source_kind.items():
                summary.source_stats(source_kind).posted = count
        if self._rejected_counted is not None:
            summary.rejected = self._rejected_counted.emit_count
        if self._output_path is not None:
            structlog.get_logger("job_ftch.cli").info(
                "pipeline_run_complete",
                output_path=str(self._output_path),
                summary=summary.as_dict(),
            )

    def _build_source_from_specs(self) -> Source[RawItem]:
        from job_ftch.infrastructure.sources.composite import CompositeSource

        child_sources = [
            cast(Source[RawItem], create_source_from_spec(spec, self._auth_provider))
            for spec in self._source_specs
        ]
        if len(child_sources) == 1:
            return child_sources[0]
        return CompositeSource(child_sources)


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


def tenant_to_settings(tenant: TenantConfig, base_settings: Settings | None = None) -> Settings:
    base = (base_settings or get_settings()).model_dump(mode="python")
    tenant_id = tenant.tenant_id
    base.update(
        {
            "tenant_id": tenant_id,
            "tenant_display_name": tenant.display_name,
            "source_backend": tenant.source_backend,
            "sink_backend": tenant.output.backend or tenant.sink_backend,
            "store_backend": tenant.store_backend,
            "job_group_store_backend": tenant.job_group_store_backend,
            "llm_backend": tenant.llm_backend,
            "posting_backend": tenant.posting_backend,
            "dry_run": tenant.dry_run,
            "metrics_enabled": tenant.metrics_enabled,
            "metrics_port": tenant.metrics_port,
            "pipeline_max_items_per_run": tenant.pipeline_max_items_per_run,
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
            "rejected_output_path": tenant.rejected_output.render_path(tenant_id),
            "rejected_output_jsonl": tenant.rejected_output.jsonl,
            "rejected_output_schema_version": tenant.rejected_output.schema_version,
            "review_max_quality_score": tenant.review_max_quality_score,
            "posting_min_quality_score": tenant.posting_min_quality_score,
            "store_path": Path(str(tenant.store_path).format(tenant_id=tenant_id)),
            "job_store_path": (
                Path(str(tenant.job_store_path).format(tenant_id=tenant_id))
                if tenant.job_store_path is not None
                else None
            ),
            "store_dsn": tenant.store_dsn,
            "store_pool_min": tenant.store_pool_min,
            "store_pool_max": tenant.store_pool_max,
            "store_fallback_on_error": tenant.store_fallback_on_error,
            "job_backend": tenant.job_backend,
            "search_backend": tenant.search_backend,
            "vector_backend": tenant.vector_backend,
            "embedding_enabled": tenant.embedding_enabled,
            "embedding_provider": tenant.embedding_provider,
            "search_language": tenant.search_language,
        }
    )
    return Settings.model_validate(base)


def configure(path: str | Path) -> PipelineBuilder:
    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

    config_path = Path(path)
    tenant = load_tenant_config(config_path)
    settings = tenant_to_settings(tenant)
    auth = EnvAuthProvider()
    store = cast(Store, create_store(settings))
    job_group_store = cast(JobGroupStore, create_job_group_store(settings))
    llm = cast("LLMProvider", create_llm(settings))
    catalog = load_profile_catalog(settings)
    sanitize_node, nodes = build_nodes(settings, store, llm, job_group_store, catalog=catalog)
    output_sink, review_sink, posting_sink = build_output_sinks(settings)
    rejected_counted, rejected_sink = build_rejected_sink(settings)

    builder = PipelineBuilder()
    builder.sources(tenant.sources)
    builder.auth(auth)
    builder.store(store)
    builder.stage(cast(ProcessingNode[Any], sanitize_node))
    for node in nodes:
        builder.stage(cast(ProcessingNode[Any], node))
    builder.sink(output_sink)
    builder.with_quarantine_sink(build_quarantine_sink(settings))
    builder.with_rejected_sink(rejected_sink, counted=rejected_counted)
    builder.set_default_max_items(settings.pipeline_max_items_per_run)
    builder.set_summary_context(
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


def build_composite_source_from_file(path: Path, store: Any = None) -> Source[RawItem]:
    from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
    from job_ftch.infrastructure.sources.composite import CompositeSource

    auth = EnvAuthProvider()
    specs = load_sources(path)
    child_sources = [create_source_from_spec(spec, auth, store=store) for spec in specs]
    return CompositeSource(cast("Sequence[Source[RawItem]]", child_sources))


def build_source(settings: Settings, store: Any = None) -> Source[RawItem]:
    if settings.sources_file_path:
        return build_composite_source_from_file(settings.sources_file_path, store=store)
    return cast(Source[RawItem], create_source(settings))


def build_classifier(_settings: Settings, _catalog: ProfileCatalog) -> ClassifierProvider | None:
    try:
        from job_ftch.infrastructure.classifiers.keyword_classifier import KeywordClassifierProvider
    except Exception:
        return None
    return KeywordClassifierProvider()


def load_profile_catalog(settings: Settings) -> ProfileCatalog:
    if settings.filter_profile_path is None:
        return ProfileCatalog.default()
    return load_catalog_from_path(settings.filter_profile_path)


def build_nodes(
    settings: Settings,
    store: Store,
    llm: LLMProvider,
    job_group_store: JobGroupStore,
    catalog: ProfileCatalog,
) -> tuple[SanitizingNode[RawItem], Sequence[Stage[Any, Any]]]:
    classifier = build_classifier(settings, catalog)
    nodes: list[Stage[Any, Any]] = [
        SourceContextNode(),
        PostTypeClassificationNode(classifier=classifier),
        HardFilterNode(catalog),
        DedupNode(store),
        SemanticPrefilterNode(catalog),
        ExtractionNode(llm),
        ExtractionValidationNode(),
        TitleCompanyNormalizationNode(),
        SkillNormalizationNode(),
        LocationWorkModeNormalizationNode(),
        CompensationParsingNode(),
        JobLifecycleNode(),
        JobAggregationNode(job_group_store, attach_group_id=True),
        MultiProfileMatchNode(catalog),
        RiskScoringNode(),
        QualityScoringNode(),
        JobValidationNode(),
        RoutingNode(
            accept_threshold=settings.routing_accept_threshold,
            review_threshold=settings.routing_review_threshold,
            quality_override_threshold=settings.routing_quality_override_threshold,
        ),
    ]

    if settings.embedding_enabled and settings.vector_backend:
        provider = cast("EmbeddingProvider", create_embedding_provider(settings))
        vector_backend = cast("VectorBackend", create_vector_backend(settings))
        if provider and vector_backend:
            from job_ftch.nodes.embedding import EmbeddingNode

            nodes.append(EmbeddingNode(provider=provider, vector_backend=vector_backend))

    return (
        SanitizeNode(
            allowed_career_site_hosts=settings.career_site_allowed_hosts,
            max_text_length=settings.pipeline_max_text_length,
        ),
        nodes,
    )


def build_sink(settings: Settings) -> Sink[JobRecord]:
    return cast(Sink[JobRecord], create_sink(settings))


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    return FailureTolerantSink(
        create_sink(settings, quarantine=True),  # type: ignore[arg-type]
        sink_name="quarantine",
    )


def build_rejected_sink(
    settings: Settings,
) -> tuple[CountedSink[RejectedItem], Sink[RejectedItem]]:
    counted: CountedSink[RejectedItem] = CountedSink(
        create_sink(settings.rejected_settings())  # type: ignore[arg-type]
    )
    return counted, FailureTolerantSink(counted, sink_name="rejected")


def build_output_sinks(
    settings: Settings,
) -> tuple[Sink[JobRecord], CountedSink[JobRecord], CountedSink[JobRecord] | None]:
    main_sink: CountedSink[JobRecord] = CountedSink(build_sink(settings))
    sink_chain: list[Sink[JobRecord]] = [main_sink]
    review_counted: CountedSink[JobRecord] = CountedSink(
        create_sink(settings.review_settings())  # type: ignore[arg-type]
    )
    sink_chain.append(
        RoutingSink(
            [(_needs_review(settings), FailureTolerantSink(review_counted, sink_name="review"))],
        )
    )
    posting_sink: CountedSink[JobRecord] | None = None
    if not settings.dry_run and settings.posting_backend != "none":
        posting_counted: CountedSink[JobRecord] = CountedSink(
            create_sink(settings.posting_settings())  # type: ignore[arg-type]
        )
        posting_sink = posting_counted
        sink_chain.append(
            RoutingSink(
                [
                    (
                        _should_post(settings),
                        FailureTolerantSink(posting_counted, sink_name="posting"),
                    )
                ],
            )
        )
    return FanOutSink(sink_chain), review_counted, posting_sink


async def build_store(settings: Settings) -> Store:
    return cast(Store, await create_store_with_fallback(settings))


def build_llm(settings: Settings) -> LLMProvider:
    return cast(LLMProvider, create_llm(settings))


def _needs_review(settings: Settings) -> Callable[[JobRecord], bool]:
    def predicate(job: JobRecord) -> bool:
        if job.routing_decision == MatchDecision.REVIEW:
            return True
        return (
            bool(job.review_reasons)
            or (job.quality_score or 0.0) < settings.review_max_quality_score
        )

    return predicate


def _should_post(settings: Settings) -> Callable[[JobRecord], bool]:
    def predicate(job: JobRecord) -> bool:
        if job.routing_decision == MatchDecision.ACCEPT:
            return True
        return (
            not job.review_reasons
            and (job.quality_score or 0.0) >= settings.posting_min_quality_score
        )

    return predicate


async def run_pipeline_from_settings(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    store = await build_store(settings)
    try:
        job_group_store = cast(JobGroupStore, await create_job_group_store_with_fallback(settings))
        llm = build_llm(settings)
        catalog = load_profile_catalog(settings)
        sanitize_node, nodes = build_nodes(settings, store, llm, job_group_store, catalog=catalog)
        output_sink, review_sink, posting_sink = build_output_sinks(settings)
        rejected_counted, rejected_sink = build_rejected_sink(settings)
        builder = (
            PipelineBuilder()
            .with_runtime_source(build_source(settings, store=store))
            .store(store)
            .stage(cast(ProcessingNode[Any], sanitize_node))
            .sink(output_sink)
            .with_quarantine_sink(build_quarantine_sink(settings))
            .with_rejected_sink(rejected_sink, counted=rejected_counted)
            .set_default_max_items(settings.pipeline_max_items_per_run)
            .set_summary_context(
                review_sink=review_sink,
                posting_sink=posting_sink,
                job_group_store=job_group_store,
                profile_name=catalog.catalog_name,
                output_path=settings.output_path,
            )
        )
        for node in nodes:
            builder.stage(cast(ProcessingNode[Any], node))
        summary = await builder.run_async(max_items=settings.pipeline_max_items_per_run)
        await store.set_run_state("pipeline.status", "finished")
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
