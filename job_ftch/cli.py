"""Command-line entry point for job_ftch."""

from __future__ import annotations

import argparse
import asyncio
import json

import structlog

from job_ftch.application.builder import (
    run_pipeline_from_settings,
    run_search_from_settings,
    show_status_from_settings,
)
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.scheduler import Scheduler
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local job_ftch pipeline.")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    pipeline_parser = subparsers.add_parser("pipeline", help="Run the extraction pipeline")
    pipeline_parser.add_argument("--daemon", action="store_true", help="Run in background loop.")
    pipeline_parser.add_argument("--status", action="store_true", help="Show last run status.")

    runs_parser = subparsers.add_parser("runs", help="Inspect persisted run history")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_subparsers.add_parser("list", help="List recent pipeline runs")
    runs_list.add_argument("--tenant", dest="tenant_id", default=None)
    runs_list.add_argument("--limit", type=int, default=20)
    runs_show = runs_subparsers.add_parser("show", help="Show one pipeline run")
    runs_show.add_argument("run_id", type=str)
    runs_show.add_argument("--tenant", dest="tenant_id", default=None)

    tenants_parser = subparsers.add_parser("tenants", help="Manage tenant configurations")
    tenants_subparsers = tenants_parser.add_subparsers(dest="tenant_command", required=True)
    tenants_subparsers.add_parser("list", help="List configured tenants")
    tenants_status = tenants_subparsers.add_parser("status", help="Show last status for one tenant")
    tenants_status.add_argument("tenant_id", type=str)
    tenants_lineage = tenants_subparsers.add_parser(
        "lineage", help="Show lineage for one job inside a tenant"
    )
    tenants_lineage.add_argument("tenant_id", type=str)
    tenants_lineage.add_argument("job_id", type=str)
    tenants_run = tenants_subparsers.add_parser("run", help="Run one tenant immediately")
    tenants_run.add_argument("tenant_id", type=str)
    tenants_reset = tenants_subparsers.add_parser("reset", help="Reset tenant store namespace")
    tenants_reset.add_argument("tenant_id", type=str)

    mcp_parser = subparsers.add_parser("mcp-server", help="Start the FastMCP tenant server")
    mcp_parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse"),
        default="stdio",
        help="MCP transport to use.",
    )
    mcp_parser.add_argument("--host", default="127.0.0.1")
    mcp_parser.add_argument("--port", type=int, default=8000)

    bot_parser = subparsers.add_parser("telegram-bot", help="Start the Telegram bot")
    bot_mode = bot_parser.add_mutually_exclusive_group()
    bot_mode.add_argument(
        "--polling",
        action="store_true",
        help="Run in long-polling mode (default).",
    )
    bot_mode.add_argument(
        "--webhook",
        action="store_true",
        help="Run in webhook mode (requires FastAPI).",
    )
    bot_parser.add_argument("--host", default="0.0.0.0", help="Webhook server host.")  # nosec B104
    bot_parser.add_argument("--port", type=int, default=8080, help="Webhook server port.")

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
        "--configs-dir",
        default=None,
        help="Directory with tenant YAML/JSON configs for multi-tenant mode.",
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


logger = structlog.get_logger(__name__)


def build_settings(args: argparse.Namespace) -> Settings:
    updates: dict[str, object] = {}
    if args.source_backend is not None:
        updates["source_backend"] = args.source_backend
    if args.sources_file is not None:
        from pathlib import Path

        updates["sources_file_path"] = Path(args.sources_file)
    if args.configs_dir is not None:
        from pathlib import Path

        updates["configs_dir"] = Path(args.configs_dir)
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
    elif args.command == "runs":
        asyncio.run(_handle_runs(settings, args))
    elif args.command == "tenants":
        asyncio.run(_handle_tenants(settings, args))
    elif args.command == "mcp-server":
        _run_mcp_server(settings, args)
    elif args.command == "telegram-bot":
        _run_telegram_bot(settings, args)
    elif args.command == "pipeline" and args.status:
        asyncio.run(show_status_from_settings(settings))
    elif args.command == "pipeline" and args.daemon:
        asyncio.run(_run_scheduler(settings))
    else:
        asyncio.run(run_pipeline_from_settings(settings))
    return 0


def _load_tenant_runner(settings: Settings) -> TenantRunner:
    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for tenant commands."
        raise ValueError(msg)
    return TenantRunner.from_tenants(load_tenants(settings.configs_dir), base_settings=settings)


def _merge_run_summaries(summaries: list[RunSummary]) -> RunSummary:
    merged = RunSummary()
    for summary in summaries:
        merged.fetched += summary.fetched
        merged.sanitized += summary.sanitized
        merged.triaged += summary.triaged
        merged.extracted += summary.extracted
        merged.partial += summary.partial
        merged.review += summary.review
        merged.duplicates += summary.duplicates
        merged.dropped += summary.dropped
        merged.emitted += summary.emitted
        merged.posted += summary.posted
        merged.rejected += summary.rejected
        merged.quarantined += summary.quarantined
        merged.failed += summary.failed
        merged.new_groups_created += summary.new_groups_created
        merged.merged_into_group += summary.merged_into_group
        merged.monitored += summary.monitored
        merged.rich_emitted += summary.rich_emitted
        merged.scraped += summary.scraped
        merged.scrape_fallback_used += summary.scrape_fallback_used
        merged.monitor_truncated += summary.monitor_truncated
        merged.source_partial = merged.source_partial or summary.source_partial

        for reason, count in summary.drop_reasons.items():
            merged.drop_reasons[reason] = merged.drop_reasons.get(reason, 0) + count
        for reason, count in summary.quarantine_reasons.items():
            merged.quarantine_reasons[reason] = merged.quarantine_reasons.get(reason, 0) + count
        for source_kind, source_stats in summary.by_source_kind.items():
            target = merged.source_stats(source_kind)
            target.fetched += source_stats.fetched
            target.sanitized += source_stats.sanitized
            target.triaged += source_stats.triaged
            target.extracted += source_stats.extracted
            target.partial += source_stats.partial
            target.review += source_stats.review
            target.duplicates += source_stats.duplicates
            target.dropped += source_stats.dropped
            target.emitted += source_stats.emitted
            target.posted += source_stats.posted
            target.rejected += source_stats.rejected
            target.quarantined += source_stats.quarantined
            target.failed += source_stats.failed
            target.new_groups_created += source_stats.new_groups_created
            target.merged_into_group += source_stats.merged_into_group
            target.monitored += source_stats.monitored
            target.rich_emitted += source_stats.rich_emitted
            target.scraped += source_stats.scraped
            target.scrape_fallback_used += source_stats.scrape_fallback_used
            target.monitor_truncated += source_stats.monitor_truncated
            target.source_partial = target.source_partial or source_stats.source_partial
            for reason, count in source_stats.drop_reasons.items():
                target.drop_reasons[reason] = target.drop_reasons.get(reason, 0) + count
            for reason, count in source_stats.quarantine_reasons.items():
                target.quarantine_reasons[reason] = target.quarantine_reasons.get(reason, 0) + count
        for source_id, source_stats in summary.by_source_id.items():
            source_kind, _, source_name = source_id.partition(":")
            target_identity = merged.source_identity_stats(source_kind, source_name)
            if target_identity is None:
                continue
            target_identity.fetched += source_stats.fetched
            target_identity.sanitized += source_stats.sanitized
            target_identity.triaged += source_stats.triaged
            target_identity.extracted += source_stats.extracted
            target_identity.partial += source_stats.partial
            target_identity.review += source_stats.review
            target_identity.duplicates += source_stats.duplicates
            target_identity.dropped += source_stats.dropped
            target_identity.emitted += source_stats.emitted
            target_identity.posted += source_stats.posted
            target_identity.rejected += source_stats.rejected
            target_identity.quarantined += source_stats.quarantined
            target_identity.failed += source_stats.failed
            target_identity.new_groups_created += source_stats.new_groups_created
            target_identity.merged_into_group += source_stats.merged_into_group
            target_identity.monitored += source_stats.monitored
            target_identity.rich_emitted += source_stats.rich_emitted
            target_identity.scraped += source_stats.scraped
            target_identity.scrape_fallback_used += source_stats.scrape_fallback_used
            target_identity.monitor_truncated += source_stats.monitor_truncated
            target_identity.source_partial = (
                target_identity.source_partial or source_stats.source_partial
            )
            for reason, count in source_stats.drop_reasons.items():
                target_identity.drop_reasons[reason] = (
                    target_identity.drop_reasons.get(reason, 0) + count
                )
            for reason, count in source_stats.quarantine_reasons.items():
                target_identity.quarantine_reasons[reason] = (
                    target_identity.quarantine_reasons.get(reason, 0) + count
                )
    return merged


async def _run_scheduler(settings: Settings) -> None:
    if settings.configs_dir is None:
        scheduler = Scheduler(settings, run_pipeline_from_settings)
        await scheduler.run_forever()
        return

    runner = _load_tenant_runner(settings)

    async def run_all_tenants(_: Settings) -> RunSummary:
        summaries = await runner.run_all()
        return _merge_run_summaries(summaries)

    scheduler = Scheduler(settings, run_all_tenants)
    try:
        await scheduler.run_forever()
    finally:
        await runner.close()


async def _handle_tenants(settings: Settings, args: argparse.Namespace) -> None:
    runner = _load_tenant_runner(settings)
    try:
        if args.tenant_command == "list":
            for tenant in await runner.list_tenants():
                print(f"{tenant.tenant_id}\t{tenant.display_name}\tsources={tenant.source_count}")
            return
        if args.tenant_command == "status":
            summary = await runner.get_status(args.tenant_id)
            print(
                "null"
                if summary is None
                else json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, default=str)
            )
            return
        if args.tenant_command == "run":
            summary = await runner.run_tenant(args.tenant_id)
            print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, default=str))
            return
        if args.tenant_command == "lineage":
            lineage = await runner.get_job_lineage(args.job_id, tenant_id=args.tenant_id)
            print(
                "null"
                if lineage is None
                else json.dumps(
                    lineage.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str
                )
            )
            return
        if args.tenant_command == "reset":
            await runner.reset_tenant(args.tenant_id)
            print(f"reset {args.tenant_id}")
            return
    finally:
        await runner.close()


async def _handle_runs(settings: Settings, args: argparse.Namespace) -> None:
    runner = _load_tenant_runner(settings)
    try:
        if args.runs_command == "list":
            summaries = await runner.list_runs(tenant_id=args.tenant_id, limit=args.limit)
            print(
                json.dumps(
                    [summary.as_dict() for summary in summaries],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return
        if args.runs_command == "show":
            summary = await runner.get_run(args.run_id, tenant_id=args.tenant_id)
            print(
                "null"
                if summary is None
                else json.dumps(summary.as_dict(), ensure_ascii=False, indent=2, default=str)
            )
            return
    finally:
        await runner.close()


async def _run_bot_with_scheduler(
    runner: TenantRunner,
    config: object,
    interval_seconds: int,
    embedding_provider: object | None = None,
    reranker: object | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run aiogram long-polling and the tenant pipeline scheduler concurrently."""
    import contextlib

    from adapters.telegram_bot.main import build_bot, build_dispatcher, configure_bot

    bot = build_bot(config)  # type: ignore[arg-type]
    await configure_bot(bot, config)  # type: ignore[arg-type]
    dispatcher = build_dispatcher(
        runner=runner,
        config=config,  # type: ignore[arg-type]
        embedding_provider=embedding_provider,  # type: ignore[arg-type]
        reranker=reranker,  # type: ignore[arg-type]
    )

    # Background warmup: load the embedding model before the first user request.
    if embedding_provider is not None:

        async def _warmup() -> None:
            with contextlib.suppress(Exception):
                await embedding_provider.embed(["warmup"])  # type: ignore[attr-defined]

        asyncio.create_task(_warmup())

    async def _scheduler_loop() -> None:
        while stop_event is None or not stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            try:
                await runner.run_all()
                logger.info("scheduled_run_complete", tenants=runner.tenant_ids())
            except Exception as exc:
                logger.error("scheduled_run_failed", error=str(exc))

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot),
            _scheduler_loop(),
        )
    finally:
        await bot.session.close()


def _run_telegram_bot(settings: Settings, args: argparse.Namespace) -> None:
    try:
        from adapters.telegram_bot.config import load_bot_config
        from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
    except ImportError:
        print("Install job-ftch[bot] to use the Telegram bot.")
        raise SystemExit(1) from None

    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for telegram-bot."
        raise ValueError(msg)

    runner = _load_tenant_runner(settings)
    auth = EnvAuthProvider()
    bot_config = load_bot_config(auth)

    embedding_provider = None
    if settings.embedding_enabled:
        from job_ftch.application.registry import create_embedding_provider

        embedding_provider = create_embedding_provider(settings)

    reranker = None
    if settings.reranker_enabled:
        from job_ftch.application.registry import create_reranker

        reranker = create_reranker(settings)

    if args.webhook:
        try:
            import uvicorn

            from adapters.telegram_bot.api import create_app
        except ImportError:
            print("Install job-ftch[api] to use webhook mode.")
            raise SystemExit(1) from None

        app = create_app(
            configs_dir=settings.configs_dir,
            base_settings=settings,
            runner=runner,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        interval = settings.schedule_interval_seconds or (4 * 3600)
        asyncio.run(
            _run_bot_with_scheduler(
                runner=runner,
                config=bot_config,
                interval_seconds=interval,
                embedding_provider=embedding_provider,
                reranker=reranker,
            )
        )


def _run_mcp_server(settings: Settings, args: argparse.Namespace) -> None:
    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for mcp-server."
        raise ValueError(msg)
    from adapters.mcp.server import create_server

    server = create_server(configs_dir=settings.configs_dir, base_settings=settings)
    asyncio.run(server.startup())
    try:
        server.run(transport=args.transport, host=args.host, port=args.port)
    finally:
        asyncio.run(server.shutdown())
