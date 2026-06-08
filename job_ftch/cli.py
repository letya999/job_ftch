"""Command-line entry point for job_ftch."""

from __future__ import annotations

import argparse
import asyncio

from job_ftch.application.builder import (
    run_pipeline_from_settings,
    run_search_from_settings,
    show_status_from_settings,
)
from job_ftch.application.scheduler import Scheduler
from job_ftch.config import Settings, get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local job_ftch pipeline.")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    pipeline_parser = subparsers.add_parser("pipeline", help="Run the extraction pipeline")
    pipeline_parser.add_argument("--daemon", action="store_true", help="Run in background loop.")
    pipeline_parser.add_argument("--status", action="store_true", help="Show last run status.")

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


def main() -> int:
    args = parse_args()
    settings = build_settings(args)

    if args.command == "search":
        asyncio.run(run_search_from_settings(settings, args))
    elif args.command == "pipeline" and args.status:
        asyncio.run(show_status_from_settings(settings))
    elif args.command == "pipeline" and args.daemon:
        scheduler = Scheduler(settings, run_pipeline_from_settings)
        asyncio.run(scheduler.run_forever())
    else:
        asyncio.run(run_pipeline_from_settings(settings))
    return 0
