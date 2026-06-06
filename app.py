"""job_ftch composition root for local phase-0 runs."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING, Any, cast

import structlog

from application.logging import configure_logging
from application.pipeline import Pipeline, RunSummary
from application.registry import create_llm, create_sink, create_source, create_store
from application.telemetry import configure_telemetry
from config import Settings, get_settings
from nodes import (
    AIRoleRelevanceNode,
    CompensationParsingNode,
    DedupNode,
    ExtractionNode,
    ExtractionValidationNode,
    HeuristicTriageNode,
    JobValidationNode,
    LocationWorkModeNormalizationNode,
    QualityScoringNode,
    SanitizeNode,
    TitleCompanyNormalizationNode,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from application.contracts import (
        LLMProvider,
        ProcessingNode,
        SanitizingNode,
        Sink,
        Source,
        Store,
    )
    from domain import Job, QuarantinedRawItem, RawItem, RejectedItem
    from sinks import CountedSink

from sinks import FanOutSink, RoutingSink


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local job_ftch debug pipeline.")
    parser.add_argument(
        "--source-backend",
        default=None,
        help="Source backend: local_fixture, telegram_channel, telegram_group, telegram_comment, career_site.",
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
        "--rejected-output-path",
        default=None,
        help="Path to rejected-items JSONL output.",
    )
    parser.add_argument(
        "--posting-backend",
        default=None,
        help="Optional posting backend, e.g. telegram_posting.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip outbound posting sinks.")
    parser.add_argument("--once", action="store_true", help="Run a single pass (default mode).")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum items to process.")
    return parser.parse_args()


def build_settings(args: argparse.Namespace) -> Settings:
    updates: dict[str, object] = {}
    if args.source_backend is not None:
        updates["source_backend"] = args.source_backend
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
    base_settings = get_settings()
    payload = base_settings.model_dump(mode="python")
    payload.update(updates)
    return Settings.model_validate(payload)


def build_source(settings: Settings) -> Source[RawItem]:
    return create_source(settings)  # type: ignore[return-value]


def build_nodes(
    settings: Settings,
    store: Store,
    llm: LLMProvider,
) -> tuple[SanitizingNode[RawItem], Sequence[ProcessingNode[object]]]:
    nodes: list[ProcessingNode[Any]] = [
        HeuristicTriageNode(),
        DedupNode(store),
        ExtractionNode(llm),
        ExtractionValidationNode(),
        TitleCompanyNormalizationNode(),
        LocationWorkModeNormalizationNode(),
        CompensationParsingNode(),
        AIRoleRelevanceNode(),
        QualityScoringNode(),
        JobValidationNode(),
    ]
    return (
        SanitizeNode(allowed_career_site_hosts=settings.career_site_allowed_hosts),
        cast("Sequence[ProcessingNode[object]]", nodes),
    )


def build_sink(settings: Settings) -> Sink[Job]:
    return create_sink(settings)  # type: ignore[return-value]


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    return create_sink(settings, quarantine=True)  # type: ignore[return-value]


def build_rejected_sink(settings: Settings) -> CountedSink[RejectedItem]:
    from sinks import CountedSink

    return CountedSink(create_sink(settings.rejected_settings()))  # type: ignore[arg-type]


def build_output_sinks(
    settings: Settings,
) -> tuple[Sink[Job], CountedSink[Job], CountedSink[Job] | None]:
    from sinks import CountedSink

    main_sink: CountedSink[Job] = CountedSink(build_sink(settings))
    sink_chain: list[Sink[Job]] = [main_sink]
    review_sink: CountedSink[Job] = CountedSink(
        create_sink(settings.review_settings())  # type: ignore[arg-type]
    )
    sink_chain.append(
        RoutingSink(
            [(_needs_review(settings), review_sink)],
        )
    )
    if not settings.dry_run and settings.posting_backend != "none":
        active_posting_sink: CountedSink[Job] = CountedSink(
            create_sink(settings.posting_settings())  # type: ignore[arg-type]
        )
        sink_chain.append(
            RoutingSink(
                [(_should_post(settings), active_posting_sink)],
            )
        )
        posting_sink: CountedSink[Job] | None = active_posting_sink
    else:
        posting_sink = None
    return FanOutSink(sink_chain), review_sink, posting_sink


def build_store(settings: Settings) -> Store:
    return create_store(settings)  # type: ignore[return-value]


def build_llm(settings: Settings) -> LLMProvider:
    return create_llm(settings)  # type: ignore[return-value]


def _needs_review(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return bool(job.review_reasons) or (
            job.quality_score or 0.0
        ) < settings.review_max_quality_score

    return predicate


def _should_post(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return not job.review_reasons and (
            job.quality_score or 0.0
        ) >= settings.posting_min_quality_score

    return predicate


async def run_pipeline(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    store = build_store(settings)
    llm = build_llm(settings)
    sanitize_node, nodes = build_nodes(settings, store, llm)
    output_sink, review_sink, posting_sink = build_output_sinks(settings)
    rejected_sink = build_rejected_sink(settings)
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
    summary.review = review_sink.emit_count
    summary.posted = posting_sink.emit_count if posting_sink is not None else 0
    summary.rejected = rejected_sink.emit_count
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


def main() -> int:
    args = parse_args()
    settings = build_settings(args)
    asyncio.run(run_pipeline(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
