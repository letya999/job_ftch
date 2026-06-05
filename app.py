"""job_ftch composition root for local phase-0 runs."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

import structlog

from application.logging import configure_logging
from application.pipeline import Pipeline, RunSummary
from application.telemetry import configure_telemetry
from config import Settings, SinkBackend, SourceBackend, StoreBackend
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.stores.in_memory import InMemoryStore
from nodes import SanitizeNode
from sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from application.contracts import Node, Sink, Source, Store
    from domain import RawItem


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local job_ftch debug pipeline.")
    parser.add_argument(
        "--source-path", default=None, help="Path to a JSON or JSONL RawItem fixture."
    )
    parser.add_argument("--output-path", default=None, help="Path to JSON or JSONL output.")
    parser.add_argument(
        "--jsonl", action="store_true", help="Write JSON Lines instead of a JSON array."
    )
    parser.add_argument("--max-items", type=int, default=None, help="Maximum items to process.")
    return parser.parse_args()


def build_settings(args: argparse.Namespace) -> Settings:
    updates: dict[str, object] = {}
    if args.source_path is not None:
        updates["debug_source_path"] = args.source_path
    if args.output_path is not None:
        updates["output_path"] = args.output_path
    if args.jsonl:
        updates["output_jsonl"] = True
    if args.max_items is not None:
        updates["pipeline_max_items_per_run"] = args.max_items
    base_settings = Settings()
    payload = base_settings.model_dump(mode="python")
    payload.update(updates)
    return Settings.model_validate(payload)


def build_source(settings: Settings) -> Source[RawItem]:
    if settings.source_backend is SourceBackend.LOCAL_FIXTURE:
        return LocalFixtureSource(settings.debug_source_path)
    msg = f"Unsupported source backend: {settings.source_backend}"
    raise ValueError(msg)


def build_nodes() -> list[Node[RawItem]]:
    return [SanitizeNode()]


def build_sink(settings: Settings) -> Sink[RawItem]:
    if settings.sink_backend is SinkBackend.JSON_FILE:
        return JsonFileSink(settings.output_path, jsonl=settings.output_jsonl)
    msg = f"Unsupported sink backend: {settings.sink_backend}"
    raise ValueError(msg)


def build_store(settings: Settings) -> Store:
    if settings.store_backend is StoreBackend.MEMORY:
        return InMemoryStore()
    msg = f"Unsupported store backend: {settings.store_backend}"
    raise ValueError(msg)


async def run_pipeline(settings: Settings) -> RunSummary:
    configure_logging(settings.log_level)
    configure_telemetry(
        settings.telemetry_service_name,
        console_exporter=settings.telemetry_console_exporter,
    )
    pipeline = Pipeline(
        source=build_source(settings),
        nodes=build_nodes(),
        sink=build_sink(settings),
        store=build_store(settings),
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
