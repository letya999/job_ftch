"""job_ftch composition root for local phase-0 runs."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

import structlog

from application.logging import configure_logging
from application.pipeline import Pipeline, RunSummary
from application.registry import create_sink, create_source, create_store
from application.telemetry import configure_telemetry
from config import Settings, get_settings
from nodes import DedupNode, HeuristicTriageNode, SanitizeNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from application.contracts import ProcessingNode, SanitizingNode, Sink, Source, Store
    from domain import QuarantinedRawItem, RawItem


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
    if args.jsonl:
        updates["output_jsonl"] = True
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
) -> tuple[SanitizingNode[RawItem], Sequence[ProcessingNode[RawItem]]]:
    return (
        SanitizeNode(allowed_career_site_hosts=settings.career_site_allowed_hosts),
        [HeuristicTriageNode(), DedupNode(store)],
    )


def build_sink(settings: Settings) -> Sink[RawItem]:
    return create_sink(settings)  # type: ignore[return-value]


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    return create_sink(settings, quarantine=True)  # type: ignore[return-value]


def build_store(settings: Settings) -> Store:
    return create_store(settings)  # type: ignore[return-value]


async def run_pipeline(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    store = build_store(settings)
    sanitize_node, nodes = build_nodes(settings, store)
    pipeline = Pipeline(
        source=build_source(settings),
        sanitize_node=sanitize_node,
        nodes=nodes,
        sink=build_sink(settings),
        store=store,
        quarantine_sink=build_quarantine_sink(settings),
    )
    summary = await pipeline.run(max_items=settings.pipeline_max_items_per_run)
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
