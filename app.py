"""job_ftch composition root for local phase-0 runs."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING, Any, cast

import structlog

from application.logging import configure_logging
from application.pipeline import Pipeline, RunSummary
from application.registry import (
    create_job_group_store,
    create_llm,
    create_sink,
    create_source,
)
from application.scheduler import Scheduler
from application.telemetry import configure_telemetry
from config import Settings, get_settings
from nodes import (
    AIRoleRelevanceNode,
    CompensationParsingNode,
    DedupNode,
    ExtractionNode,
    ExtractionValidationNode,
    HeuristicTriageNode,
    JobAggregationNode,
    JobValidationNode,
    LocationWorkModeNormalizationNode,
    QualityScoringNode,
    SanitizeNode,
    TitleCompanyNormalizationNode,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from application.contracts import (
        EmbeddingProvider,
        JobGroupStore,
        LLMProvider,
        ProcessingNode,
        SanitizingNode,
        SearchBackend,
        Sink,
        Source,
        Store,
        VectorBackend,
    )
    from domain import FilterProfile, Job, QuarantinedRawItem, RawItem, RejectedItem
    from sinks import CountedSink

from sinks import FailureTolerantSink, FanOutSink, RoutingSink


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local job_ftch debug pipeline.")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Pipeline subcommand
    pipeline_parser = subparsers.add_parser("pipeline", help="Run the extraction pipeline")
    pipeline_parser.add_argument("--daemon", action="store_true", help="Run in background loop.")
    pipeline_parser.add_argument("--status", action="store_true", help="Show last run status.")

    # We add arguments to the main parser to keep backward compatibility
    # (so `python app.py --source-backend X` still works).
    parser.add_argument(
        "--source-backend",
        default=None,
        help="Source backend: local_fixture, telegram_channel, telegram_group, telegram_comment, career_site.",
    )
    parser.add_argument(
        "--sources-file",
        default=None,
        help="Path to YAML or JSON file with a list of source configs.",
    )
    parser.add_argument(
        "--source-path", default=None, help="Path to a JSON or JSONL RawItem fixture."
    )
    parser.add_argument(
        "--telegram-entity",
        default=None,
        help="Telegram channel/group username or invite-style entity for Telegram sources.",
    )
    parser.add_argument(
        "--career-site-url",
        default=None,
        help="Career-site URL for auto-detected career site parsing.",
    )
    parser.add_argument("--output-path", default=None, help="Path to JSON or JSONL output.")
    parser.add_argument(
        "--jsonl", action="store_true", help="Write JSON Lines instead of a JSON array."
    )
    parser.add_argument("--review-output-path", default=None, help="Path to review JSONL output.")
    parser.add_argument(
        "--rejected-output-path", default=None, help="Path to rejected-items JSONL output."
    )
    parser.add_argument(
        "--posting-backend", default=None, help="Optional posting backend, e.g. telegram_posting."
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip outbound posting sinks.")
    parser.add_argument("--once", action="store_true", help="Run a single pass (default mode).")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum items to process.")

    # Search parser
    search_parser = subparsers.add_parser("search", help="Search the job catalog")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--limit", type=int, default=20, help="Maximum results to return")
    search_parser.add_argument(
        "--backend",
        type=str,
        default=None,
        help="Search backend to use (e.g. sqlite, postgres, hybrid)",
    )
    search_parser.add_argument("--json", action="store_true", help="Output results as JSON")
    search_parser.add_argument(
        "--output", type=str, default=None, help="Write canonical jobs to JSONL file"
    )

    return parser.parse_args()


def build_settings(args: argparse.Namespace) -> Settings:
    updates: dict[str, object] = {}
    if args.source_backend is not None:
        updates["source_backend"] = args.source_backend
    if args.sources_file is not None:
        from pathlib import Path

        updates["sources_file_path"] = Path(args.sources_file)
    if args.source_path is not None:
        updates["source_backend"] = "local_fixture"
        updates["debug_source_path"] = args.source_path
    if args.telegram_entity is not None:
        if args.source_backend is None:
            updates["source_backend"] = "telegram_channel"
        updates["telegram_entity"] = args.telegram_entity
    if args.career_site_url is not None:
        if args.source_backend is None:
            updates["source_backend"] = "career_site"
        updates["career_site_url"] = args.career_site_url
    if args.output_path is not None:
        updates["output_path"] = args.output_path
    if args.review_output_path is not None:
        updates["review_output_path"] = args.review_output_path
    if args.rejected_output_path is not None:
        updates["rejected_output_path"] = args.rejected_output_path
    if args.posting_backend is not None:
        updates["posting_backend"] = args.posting_backend
    if args.jsonl:
        updates["output_jsonl"] = True
    if args.dry_run:
        updates["dry_run"] = True
    if args.max_items is not None:
        updates["pipeline_max_items_per_run"] = args.max_items
    if hasattr(args, "backend") and args.backend is not None:
        updates["search_backend"] = args.backend
    base_settings = get_settings()
    payload = base_settings.model_dump(mode="python")
    payload.update(updates)
    return Settings.model_validate(payload)


def build_composite_source_from_file(path: Path) -> Source[RawItem]:
    from application.registry import create_source_from_spec
    from application.source_loader import load_sources
    from infrastructure.auth.env_auth import EnvAuthProvider
    from infrastructure.sources.composite import CompositeSource

    auth = EnvAuthProvider()
    specs = load_sources(path)
    child_sources = [create_source_from_spec(spec, auth) for spec in specs]
    return CompositeSource(cast("Sequence[Source[RawItem]]", child_sources))


def build_source(settings: Settings) -> Source[RawItem]:
    if settings.sources_file_path:
        return build_composite_source_from_file(settings.sources_file_path)
    return create_source(settings)  # type: ignore[return-value]


def load_filter_profile(settings: Settings) -> FilterProfile | None:
    if settings.filter_profile_path is None:
        return None
    from application.filter_profile_loader import load_filter_profile as _load

    return _load(settings.filter_profile_path)


def build_nodes(
    settings: Settings,
    store: Store,
    llm: LLMProvider,
    job_group_store: JobGroupStore,
    profile: FilterProfile | None = None,
) -> tuple[SanitizingNode[RawItem], Sequence[ProcessingNode[object]]]:
    nodes: list[ProcessingNode[Any]] = [
        HeuristicTriageNode(profile=profile),
        DedupNode(store),
        ExtractionNode(llm),
        ExtractionValidationNode(),
        TitleCompanyNormalizationNode(),
        LocationWorkModeNormalizationNode(),
        CompensationParsingNode(),
        AIRoleRelevanceNode(profile=profile),
        QualityScoringNode(),
        JobValidationNode(),
        JobAggregationNode(job_group_store, attach_group_id=True),
    ]

    if settings.embedding_enabled and settings.vector_backend:
        from application.registry import create_embedding_provider, create_vector_backend
        from nodes.embedding import EmbeddingNode

        provider = cast("EmbeddingProvider", create_embedding_provider(settings))
        vector_backend = cast("VectorBackend", create_vector_backend(settings))
        if vector_backend:
            nodes.append(EmbeddingNode(provider=provider, vector_backend=vector_backend))

    return (
        SanitizeNode(
            allowed_career_site_hosts=settings.career_site_allowed_hosts,
            max_text_length=settings.pipeline_max_text_length,
        ),
        cast("Sequence[ProcessingNode[object]]", nodes),
    )


def build_sink(settings: Settings) -> Sink[Job]:
    return create_sink(settings)  # type: ignore[return-value]


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    return FailureTolerantSink(
        create_sink(settings, quarantine=True),  # type: ignore[arg-type]
        sink_name="quarantine",
    )


def build_rejected_sink(
    settings: Settings,
) -> tuple[CountedSink[RejectedItem], Sink[RejectedItem]]:
    from sinks import CountedSink

    counted: CountedSink[RejectedItem] = CountedSink(
        create_sink(settings.rejected_settings())  # type: ignore[arg-type]
    )
    return counted, FailureTolerantSink(counted, sink_name="rejected")


def build_output_sinks(
    settings: Settings,
) -> tuple[Sink[Job], CountedSink[Job], CountedSink[Job] | None]:
    from sinks import CountedSink

    main_sink: CountedSink[Job] = CountedSink(build_sink(settings))
    sink_chain: list[Sink[Job]] = [main_sink]
    review_counted: CountedSink[Job] = CountedSink(
        create_sink(settings.review_settings())  # type: ignore[arg-type]
    )
    sink_chain.append(
        RoutingSink(
            [(_needs_review(settings), FailureTolerantSink(review_counted, sink_name="review"))],
        )
    )
    if not settings.dry_run and settings.posting_backend != "none":
        posting_counted: CountedSink[Job] = CountedSink(
            create_sink(settings.posting_settings())  # type: ignore[arg-type]
        )
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
        posting_sink: CountedSink[Job] | None = posting_counted
    else:
        posting_sink = None
    return FanOutSink(sink_chain), review_counted, posting_sink


async def build_store(settings: Settings) -> Store:
    from application.registry import create_store_with_fallback

    return cast("Store", await create_store_with_fallback(settings))


def build_llm(settings: Settings) -> LLMProvider:
    return create_llm(settings)  # type: ignore[return-value]


def _needs_review(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return (
            bool(job.review_reasons)
            or (job.quality_score or 0.0) < settings.review_max_quality_score
        )

    return predicate


def _should_post(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return (
            not job.review_reasons
            and (job.quality_score or 0.0) >= settings.posting_min_quality_score
        )

    return predicate


async def run_pipeline(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    store = await build_store(settings)
    try:
        job_group_store = cast("JobGroupStore", create_job_group_store(settings))
        llm = build_llm(settings)
        profile = load_filter_profile(settings)
        sanitize_node, nodes = build_nodes(settings, store, llm, job_group_store, profile=profile)
        output_sink, review_sink, posting_sink = build_output_sinks(settings)
        rejected_counted, rejected_sink = build_rejected_sink(settings)
        pipeline = Pipeline(
            source=build_source(settings),
            sanitize_node=sanitize_node,
            nodes=nodes,
            sink=output_sink,
            store=store,
            quarantine_sink=build_quarantine_sink(settings),
            rejected_sink=rejected_sink,
        )
        summary = await pipeline.run(max_items=settings.pipeline_max_items_per_run)
    finally:
        _close = getattr(store, "close", None)
        if callable(_close):
            await _close()
    summary.applied_profile = profile.name if profile is not None else "default"

    # RM-090: save status to store
    await store.set_run_state("pipeline.status", "finished")
    if summary.finished_at:
        await store.set_run_state("pipeline.finished_at", summary.finished_at.isoformat())
    await store.set_run_state("pipeline.emitted", str(summary.emitted))

    # Aggregation stats
    if hasattr(job_group_store, "new_groups_created"):
        summary.new_groups_created = getattr(job_group_store, "new_groups_created", 0)
        summary.merged_into_group = getattr(job_group_store, "merged_into_group", 0)
        for source_kind, count in getattr(job_group_store, "by_source_kind_new", {}).items():
            summary.source_stats(source_kind).new_groups_created = count
        for source_kind, count in getattr(job_group_store, "by_source_kind_merged", {}).items():
            summary.source_stats(source_kind).merged_into_group = count

    summary.review = review_sink.emit_count
    summary.posted = posting_sink.emit_count if posting_sink is not None else 0
    summary.rejected = rejected_counted.emit_count
    for source_kind, count in review_sink.by_source_kind.items():
        summary.source_stats(source_kind).review = count
    if posting_sink is not None:
        for source_kind, count in posting_sink.by_source_kind.items():
            summary.source_stats(source_kind).posted = count
    structlog.get_logger("job_ftch.app").info(
        "app_run_complete",
        output_path=str(settings.output_path),
        summary=summary.as_dict(),
    )
    return summary


async def run_search(settings: Settings, args: argparse.Namespace) -> None:
    from application.registry import create_search_backend

    configure_logging(settings.log_level)
    search_backend = create_search_backend(settings)

    try:
        backend = cast("SearchBackend", search_backend)
        results = await backend.search(args.query, limit=args.limit)

        if args.json:
            import json

            print(
                json.dumps(
                    [g.model_dump(mode="json") for g in results], ensure_ascii=False, indent=2
                )
            )
        elif args.output:
            import json

            with open(args.output, "w", encoding="utf-8") as f:
                for group in results:
                    f.write(
                        json.dumps(group.canonical_job.model_dump(mode="json"), ensure_ascii=False)
                        + "\n"
                    )
            print(f"Wrote {len(results)} canonical jobs to {args.output}")
        else:
            print(f"Found {len(results)} job groups:\n")
            for g in results:
                print(
                    f"- {g.canonical_job.title} at {g.canonical_job.company} ({g.source_count} sources)"
                )
                if g.canonical_job.description:
                    desc_preview = g.canonical_job.description[:100].replace("\n", " ")
                    print(f"  {desc_preview}...")
                print()
    finally:
        _close = getattr(search_backend, "close", None)
        if callable(_close):
            await _close()


async def show_status(settings: Settings) -> None:
    from application.registry import create_store_with_fallback

    store = cast("Store", await create_store_with_fallback(settings))
    status = await store.get_run_state("pipeline.status")
    finished_at = await store.get_run_state("pipeline.finished_at")
    emitted = await store.get_run_state("pipeline.emitted")

    if status:
        print(f"Pipeline status: {status}")
        print(f"Last finished at: {finished_at or 'unknown'}")
        print(f"Items emitted: {emitted or 0}")
    else:
        print("No run status found in store.")

    _close = getattr(store, "close", None)
    if callable(_close):
        await _close()


def main() -> int:
    args = parse_args()
    settings = build_settings(args)

    if args.command == "search":
        asyncio.run(run_search(settings, args))
    elif args.command == "pipeline" and args.status:
        asyncio.run(show_status(settings))
    elif args.command == "pipeline" and args.daemon:
        scheduler = Scheduler(settings, run_pipeline)
        asyncio.run(scheduler.run_forever())
    else:
        # Default or "pipeline"
        asyncio.run(run_pipeline(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
