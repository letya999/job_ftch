"""Command-line entry point for job_ftch.

Subcommands:

- ``run --config <path>``     — modern, declarative. Run a tenant YAML once.
- ``validate --config <path>`` — modern. Parse + build only, no I/O.
- ``eval [--fixture ...]``     — modern. Regression gate (TD-002).
- ``pipeline``                 — legacy one-shot pipeline (back-compat).
- ``tenants {list,status,run,lineage,reset}`` — legacy multi-tenant.
- ``runs {list,show}``         — legacy run history.
- ``search <query>``           — legacy job catalog search.
- ``mcp-server``, ``telegram-bot`` — adapter entry points.

`uv sync` installs this as the ``job_ftch`` console script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from job_ftch.application.builder import (
    configure,
    run_pipeline_from_settings,
    run_search_from_settings,
    show_status_from_settings,
)
from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.scheduler import Scheduler
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Async pipeline for ingesting job vacancies from Telegram and career sites. "
            "See docs/quickstart.md for a 5-minute first-result walkthrough."
        ),
    )
    # ``command`` is optional: when omitted, the legacy one-shot pipeline
    # runs with the legacy ``--source-path`` / ``--output-path`` / etc.
    # flags. This keeps ``python -m job_ftch --source-path X`` working.
    sub = parser.add_subparsers(dest="command", required=False)

    # ---- Modern, declarative subcommands (preferred path) -----------------

    run_p = sub.add_parser(
        "run",
        help="Run the pipeline once from a tenant config file (preferred path).",
    )
    run_p.add_argument(
        "--config",
        "-c",
        required=True,
        type=Path,
        help="Path to a tenant YAML file (see docs/examples/sources.example.yaml).",
    )
    run_p.add_argument("--max-items", type=int, default=None)
    run_p.add_argument(
        "--json",
        action="store_true",
        help="Print RunSummary as JSON to stdout instead of human-readable.",
    )

    validate_p = sub.add_parser(
        "validate",
        help="Validate a tenant config file without running it.",
    )
    validate_p.add_argument("--config", "-c", required=True, type=Path)

    graph_p = sub.add_parser(
        "graph", help="Compile and inspect an experiment graph without running it."
    )
    graph_p.add_argument("--config", "-c", required=True, type=Path)
    graph_p.add_argument("--format", choices=("json", "dot"), default="json")
    graph_p.add_argument("--print-graph", action="store_true")
    graph_p.add_argument(
        "--compile-only", action="store_true", help="Explicitly document no model/data loading."
    )

    eval_p = sub.add_parser(
        "eval",
        help="Run the eval harness (TD-002). Wraps scripts/eval_all.sh.",
    )
    eval_p.add_argument(
        "--fixture",
        choices=("classification", "extraction"),
        help="Run only one of the two harnesses. Default: both.",
    )

    # ---- Legacy subcommands (kept for back-compat and existing tests) ----

    pipeline_parser = sub.add_parser("pipeline", help="Run the extraction pipeline (legacy flags).")
    pipeline_parser.add_argument("--daemon", action="store_true", help="Run in background loop.")
    pipeline_parser.add_argument("--status", action="store_true", help="Show last run status.")

    runs_parser = sub.add_parser("runs", help="Inspect persisted run history")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_subparsers.add_parser("list", help="List recent pipeline runs")
    runs_list.add_argument("--tenant", dest="tenant_id", default=None)
    runs_list.add_argument("--limit", type=int, default=20)
    runs_show = runs_subparsers.add_parser("show", help="Show one pipeline run")
    runs_show.add_argument("run_id", type=str)
    runs_show.add_argument("--tenant", dest="tenant_id", default=None)

    tenants_parser = sub.add_parser("tenants", help="Manage tenant configurations")
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

    mcp_parser = sub.add_parser("mcp-server", help="Start the FastMCP tenant server")
    # SUPPRESS keeps a parent-level --configs-dir value when the flag is only
    # provided before the subcommand (argparse otherwise overwrites with None).
    mcp_parser.add_argument(
        "--configs-dir",
        default=argparse.SUPPRESS,
        help="Tenant config directory (or set JOB_FTCH_CONFIGS_DIR). Also accepted as a global flag.",
    )
    mcp_parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse", "streamable-http"),
        default="stdio",
    )
    mcp_parser.add_argument("--host", default="127.0.0.1")
    mcp_parser.add_argument("--port", type=int, default=8000)

    bot_parser = sub.add_parser("telegram-bot", help="Start the Telegram bot")
    bot_mode = bot_parser.add_mutually_exclusive_group()
    bot_mode.add_argument(
        "--polling", action="store_true", help="Run in long-polling mode (default)."
    )
    bot_mode.add_argument("--webhook", action="store_true", help="Webhook mode (FastAPI).")
    # Webhook mode is an explicitly network-facing deployment command.
    bot_parser.add_argument("--host", default="0.0.0.0")  # nosec B104
    bot_parser.add_argument("--port", type=int, default=8080)

    # ---- Legacy one-shot flags (also kept for back-compat) ---------------

    parser.add_argument(
        "--source-backend",
        default=None,
        help="(legacy) Source backend: local_fixture, telegram_channel, etc.",
    )
    parser.add_argument("--sources-file", default=None)
    parser.add_argument("--configs-dir", default=None)
    parser.add_argument("--source-path", default=None)
    parser.add_argument("--telegram-entity", default=None)
    parser.add_argument("--career-site-url", default=None)
    parser.add_argument("--output-path", default=None)
    parser.add_argument("--jsonl", action="store_true")
    parser.add_argument("--review-output-path", default=None)
    parser.add_argument("--rejected-output-path", default=None)
    parser.add_argument("--posting-backend", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)

    search_parser = sub.add_parser("search", help="Search the job catalog")
    search_parser.add_argument("query", type=str)
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--backend", type=str, default=None)
    search_parser.add_argument("--json", action="store_true")
    search_parser.add_argument("--output", type=str, default=None)

    return parser


def build_settings(args: argparse.Namespace) -> Settings:
    """Translate legacy CLI flags into Settings overrides."""
    updates: dict[str, object] = {}
    if getattr(args, "source_backend", None) is not None:
        updates["source_backend"] = args.source_backend
    if getattr(args, "sources_file", None) is not None:
        updates["sources_file_path"] = Path(args.sources_file)
    if getattr(args, "configs_dir", None) is not None:
        updates["configs_dir"] = Path(args.configs_dir)
    if getattr(args, "source_path", None) is not None:
        updates["source_backend"] = "local_fixture"
        updates["debug_source_path"] = args.source_path
    if getattr(args, "telegram_entity", None) is not None:
        if getattr(args, "source_backend", None) is None:
            updates["source_backend"] = "telegram_channel"
        updates["telegram_entity"] = args.telegram_entity
    if getattr(args, "career_site_url", None) is not None:
        if getattr(args, "source_backend", None) is None:
            updates["source_backend"] = "career_site"
        updates["career_site_url"] = args.career_site_url
    if getattr(args, "output_path", None) is not None:
        updates["output_path"] = args.output_path
    if getattr(args, "review_output_path", None) is not None:
        updates["review_output_path"] = args.review_output_path
    if getattr(args, "rejected_output_path", None) is not None:
        updates["rejected_output_path"] = args.rejected_output_path
    if getattr(args, "posting_backend", None) is not None:
        updates["posting_backend"] = args.posting_backend
    if getattr(args, "jsonl", False):
        updates["output_jsonl"] = True
    if getattr(args, "dry_run", False):
        updates["dry_run"] = True
    if getattr(args, "max_items", None) is not None:
        updates["pipeline_max_items_per_run"] = args.max_items
    if hasattr(args, "backend") and getattr(args, "backend", None) is not None:
        updates["search_backend"] = args.backend

    base_settings = get_settings()
    payload = base_settings.model_dump(mode="python")
    payload.update(updates)
    return Settings.model_validate(payload)


# ---------------------------------------------------------------------------
# Modern subcommand handlers (preferred path)
# ---------------------------------------------------------------------------


def _print_summary(summary: Any, as_json: bool) -> None:
    if as_json:
        json.dump(
            summary.as_dict() if hasattr(summary, "as_dict") else {"summary": str(summary)},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        sys.stdout.write("\n")
        return
    print(
        f"Run finished: emitted={getattr(summary, 'emitted', '?')}, "
        f"rejected={getattr(summary, 'rejected', '?')}, "
        f"failed={getattr(summary, 'failed', '?')}, "
        f"duration={getattr(summary, 'duration_seconds', '?')}s"
    )


def _cmd_run_modern(settings: Settings, args: argparse.Namespace) -> int:
    if not args.config.exists():
        print(f"ERROR: config not found: {args.config}", file=sys.stderr)
        return 2

    from job_ftch.application.builder import load_tenant_config
    from job_ftch.application.tenant_runner import TenantRunner

    tenant = load_tenant_config(args.config)
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    async def _run() -> None:
        try:
            summary = await runner.run_tenant(tenant.tenant_id, max_items=args.max_items)
            _print_summary(summary, as_json=args.json)
        finally:
            await runner.close()

    asyncio.run(_run())
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    if not args.config.exists():
        print(f"ERROR: config not found: {args.config}", file=sys.stderr)
        return 2
    try:
        configure(args.config)
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.config} parses and produces a valid PipelineBuilder.")
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    from job_ftch.application.graph import compile_graph, load_graph
    from job_ftch.application.graph.inspection import print_graph

    if not args.config.exists():
        print(f"ERROR: graph config not found: {args.config}", file=sys.stderr)
        return 2
    try:
        compiled = compile_graph(load_graph(args.config))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(print_graph(compiled, args.format))
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    """Forward to the per-harness Python scripts."""
    if args.fixture is None or args.fixture == "classification":
        r = subprocess.run(
            [sys.executable, "scripts/evaluate_classification.py", "--gate"],
            check=False,
        )
        if r.returncode != 0:
            return r.returncode
    if args.fixture is None or args.fixture == "extraction":
        r = subprocess.run(
            [sys.executable, "scripts/evaluate_extraction.py", "--gate"],
            check=False,
        )
        if r.returncode != 0:
            return r.returncode
    return 0


# ---------------------------------------------------------------------------
# Legacy handlers (kept for back-compat and existing tests)
# ---------------------------------------------------------------------------


def _load_tenant_runner(settings: Settings) -> TenantRunner:
    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for tenant commands."
        raise ValueError(msg)
    return TenantRunner.from_tenants(load_tenants(settings.configs_dir), base_settings=settings)


def _merge_run_summaries(summaries: list[RunSummary]) -> RunSummary:
    """Merge a sequence of RunSummary objects into one aggregated summary."""
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
        for source_id, id_stats in summary.by_source_id.items():
            # ``source_id`` is the composite key "{kind}:{name}" produced by
            # ``RunSummary.source_identity_stats``. Split it back to look up
            # the same key in the merged summary.
            if ":" in source_id:
                id_kind, _, id_name = source_id.partition(":")
            else:
                id_kind, id_name = source_id, ""
            target_opt = merged.source_identity_stats(id_kind, id_name)
            if target_opt is None:
                continue
            id_target: SourceRunStats = target_opt
            id_target.fetched += id_stats.fetched
            id_target.sanitized += id_stats.sanitized
            id_target.triaged += id_stats.triaged
            id_target.extracted += id_stats.extracted
            id_target.partial += id_stats.partial
            id_target.review += id_stats.review
            id_target.duplicates += id_stats.duplicates
            id_target.dropped += id_stats.dropped
            id_target.emitted += id_stats.emitted
            id_target.posted += id_stats.posted
            id_target.rejected += id_stats.rejected
            id_target.quarantined += id_stats.quarantined
            id_target.failed += id_stats.failed
            id_target.new_groups_created += id_stats.new_groups_created
            id_target.merged_into_group += id_stats.merged_into_group
            id_target.monitored += id_stats.monitored
            id_target.rich_emitted += id_stats.rich_emitted
            id_target.scraped += id_stats.scraped
            id_target.scrape_fallback_used += id_stats.scrape_fallback_used
            id_target.monitor_truncated += id_stats.monitor_truncated
            id_target.source_partial = id_target.source_partial or id_stats.source_partial
            for reason, count in id_stats.drop_reasons.items():
                id_target.drop_reasons[reason] = id_target.drop_reasons.get(reason, 0) + count
            for reason, count in id_stats.quarantine_reasons.items():
                id_target.quarantine_reasons[reason] = (
                    id_target.quarantine_reasons.get(reason, 0) + count
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


def _run_telegram_bot(settings: Settings, args: argparse.Namespace) -> None:
    try:
        from job_ftch.adapters.telegram_bot.config import load_bot_config
        from job_ftch.adapters.telegram_bot.main import start_polling
        from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
    except ImportError:
        print("Install job-ftch[bot] to use the Telegram bot.")
        raise SystemExit(1) from None

    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for telegram-bot."
        raise ValueError(msg)

    # Configure telemetry before building the runner. Tenant loading, ontology
    # merge and backend wiring all log — and used to do so through structlog's
    # unconfigured PrintLogger, which bypasses the stdlib handler that ships to
    # OpenObserve. The startup window was therefore invisible in the very place
    # an operator looks when the bot fails to come up.
    from job_ftch.application.logging import configure_logging
    from job_ftch.infrastructure.observability import configure_observability

    configure_logging(settings.log_level)
    configure_observability(settings)

    runner = _load_tenant_runner(settings)
    auth = EnvAuthProvider()
    bot_config = load_bot_config(auth)

    if args.webhook:
        try:
            import uvicorn

            from job_ftch.adapters.telegram_bot.api import create_app
        except ImportError:
            print("Install job-ftch[api] to use webhook mode.")
            raise SystemExit(1) from None
        app = create_app(configs_dir=settings.configs_dir, runner=runner)
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        asyncio.run(start_polling(runner=runner, config=bot_config))


def _run_mcp_server(settings: Settings, args: argparse.Namespace) -> None:
    if settings.configs_dir is None:
        msg = "--configs-dir or JOB_FTCH_CONFIGS_DIR is required for mcp-server."
        raise ValueError(msg)
    from job_ftch.adapters.mcp.server import create_server, prepare_stdio_logging
    from job_ftch.application.logging import configure_logging

    # Configure logging before tenant load. For stdio, keep stdout protocol-clean
    # (JSON-RPC) by forcing application logs onto stderr.
    if args.transport == "stdio":
        prepare_stdio_logging(settings.log_level)
    else:
        configure_logging(settings.log_level)

    server = create_server(configs_dir=settings.configs_dir, base_settings=settings)
    asyncio.run(server.startup())
    try:
        server.run(transport=args.transport, host=args.host, port=args.port)
    finally:
        asyncio.run(server.shutdown())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = build_settings(args)

    if args.command == "run":
        return _cmd_run_modern(settings, args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "graph":
        return _cmd_graph(args)
    if args.command == "eval":
        return _cmd_eval(args)
    if args.command == "search":
        asyncio.run(run_search_from_settings(settings, args))
        return 0
    if args.command == "runs":
        asyncio.run(_handle_runs(settings, args))
        return 0
    if args.command == "tenants":
        asyncio.run(_handle_tenants(settings, args))
        return 0
    if args.command == "mcp-server":
        _run_mcp_server(settings, args)
        return 0
    if args.command == "telegram-bot":
        _run_telegram_bot(settings, args)
        return 0
    if args.command == "pipeline" and args.status:
        asyncio.run(show_status_from_settings(settings))
        return 0
    if args.command == "pipeline" and args.daemon:
        asyncio.run(_run_scheduler(settings))
        return 0
    if args.command == "pipeline":
        asyncio.run(run_pipeline_from_settings(settings))
        return 0
    # No subcommand: legacy one-shot pipeline with the bare flags.
    asyncio.run(run_pipeline_from_settings(settings))
    return 0


__all__ = [
    "main",
    "_handle_tenants",
    "_handle_runs",
    "_load_tenant_runner",
    "_merge_run_summaries",
]
