"""Run the bot ingest and filtering path without sending Telegram messages."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="ai_jobs")
    parser.add_argument("--user-id")
    parser.add_argument("--runtime", choices=("dev", "prod"), default="prod")
    parser.add_argument(
        "--configs-dir",
        type=Path,
        help="Tenant configuration directory; overrides JOB_FTCH_CONFIGS_DIR for this run.",
    )
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        help="Run only this canonical source ID; repeat for multiple sources.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Write the complete JSON run report atomically to this path.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep prior run data. Scripted ingest is clean by default.",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Clear prior run data and output artifacts without starting ingest.",
    )
    return parser


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _clear_output_artifacts(settings: Any) -> int:
    """Remove prior run outputs and known atomic-write sidecars."""
    removed = 0
    paths = {
        Path(path)
        for path in (
            settings.output_path,
            settings.review_output_path,
            settings.rejected_output_path,
            settings.quarantine_output_path,
        )
        if path is not None
    }
    for path in paths:
        suffix = "".join(path.suffixes)
        stem = path.name[: -len(suffix)] if suffix else path.name
        candidates = {path, path.with_name(f"{stem}.tmp{suffix}")}
        if path.parent.exists():
            candidates.update(path.parent.glob(f"{stem}.*.staging.jsonl"))
            candidates.update(path.parent.glob(f"{stem}.*.tmp{suffix}"))
        for candidate in candidates:
            if candidate.is_file():
                candidate.unlink()
                removed += 1
    return removed


def _select_runtime(runtime: str) -> None:
    from dotenv import load_dotenv

    os.environ["JOB_FTCH_ENV"] = runtime
    root = Path(__file__).resolve().parent.parent
    # Mirror docker-compose.dev/prod: root pipeline env first, adapter wiring
    # second, while preserving explicit shell/CI variables as the authority.
    for env_path in (
        root / ".env",
        root / f".env.{runtime}",
        root / "job_ftch" / "adapters" / "telegram_bot" / f".env.{runtime}",
    ):
        load_dotenv(env_path, override=False)
    os.environ["JOB_FTCH_RUNTIME_CONFIG_PATH"] = os.pathsep.join(
        (
            "config/runtime.yaml",
            f"config/runtime.{runtime}.yaml",
            f"job_ftch/adapters/telegram_bot/runtime.{runtime}.yaml",
        )
    )


async def _resolve_user_id(runner: Any, tenant_id: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    publish_user_id = await runner.get_publish_user_id(tenant_id)
    if publish_user_id:
        return str(publish_user_id)
    users = await runner.get_runtime(tenant_id).store.list_candidate_profile_users()
    if len(users) == 1:
        return str(users[0])
    raise ValueError("Pass --user-id: no unique candidate-profile owner could be inferred.")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from job_ftch.adapters.telegram_bot.handlers.pipeline import (
        job_passes_bot_publish_gates,
        publish_candidate_fetch_limit,
    )
    from job_ftch.application.logging import configure_logging
    from job_ftch.application.tenant_loader import load_tenants
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.config import get_settings
    from job_ftch.infrastructure.observability import configure_observability

    settings = get_settings()
    if args.configs_dir is not None:
        settings = settings.model_copy(update={"configs_dir": args.configs_dir})
    if args.clean_only and args.no_clean:
        raise ValueError("--clean-only and --no-clean cannot be used together.")
    if settings.configs_dir is None:
        raise ValueError("JOB_FTCH_CONFIGS_DIR is required.")
    configure_logging(settings.log_level)
    if not args.preflight:
        configure_observability(settings)
    tenants = load_tenants(settings.configs_dir)
    runner_settings = settings
    if args.preflight:
        runner_settings = settings.model_copy(
            update={
                "embedding_enabled": False,
                "embedding_prefilter_enabled": False,
                "bgem3_enabled": False,
            }
        )
    runner = TenantRunner.from_tenants(tenants, base_settings=runner_settings)
    try:
        user_id = await _resolve_user_id(runner, args.tenant, args.user_id)
        if not await runner.has_candidate_profile_data(args.tenant, user_id):
            raise ValueError("The selected user has no candidate profile examples.")

        sources = await runner.list_sources(args.tenant)
        requested_source_ids = set(args.source_ids or ())
        active_sources = [
            source
            for source in sources
            if source.get("enabled") is True
            and (not requested_source_ids or str(source.get("source_id")) in requested_source_ids)
        ]
        found_source_ids = {str(source.get("source_id")) for source in active_sources}
        missing_source_ids = requested_source_ids - found_source_ids
        if missing_source_ids:
            raise ValueError(
                f"Requested sources are missing or disabled: {sorted(missing_source_ids)}"
            )
        disabled_sources = [source for source in sources if source.get("enabled") is not True]
        source_count = len(active_sources)
        telemetry = {
            "langfuse_configured": bool(
                settings.tracing_enabled
                and settings.langfuse_host
                and settings.langfuse_public_key
                and settings.langfuse_secret_key
            ),
            "openobserve_configured": bool(
                settings.openobserve_enabled
                and settings.openobserve_url
                and settings.openobserve_username
                and settings.openobserve_password
            ),
        }
        if args.preflight:
            preflight_result: dict[str, Any] = {
                "tenant_id": args.tenant,
                "runtime": args.runtime,
                "source_count": source_count,
                "sources": [source["source_id"] for source in active_sources],
                "disabled_sources": [source["source_id"] for source in disabled_sources],
                "profile_ready": True,
                "telemetry": telemetry,
            }
            # Check relevance prefilter model availability
            try:
                from job_ftch.nodes.tfidf_logreg_prefilter import (
                    TfidfLogregRelevancePrefilterNode,
                )

                prefilter_node = TfidfLogregRelevancePrefilterNode()
                preflight_result["relevance_prefilter"] = prefilter_node.preflight_status()
            except Exception:
                preflight_result["relevance_prefilter"] = {"status": "unavailable"}
            return preflight_result

        reset = None
        if not args.no_clean:
            reset = await runner.clear_run_data(args.tenant)
            reset["output_artifacts"] = _clear_output_artifacts(
                runner.get_runtime(args.tenant).settings
            )
        if args.clean_only:
            return {
                "tenant_id": args.tenant,
                "runtime": args.runtime,
                "source_count": source_count,
                "reset": reset,
                "clean_only": True,
                "telemetry": telemetry,
            }
        summary = await runner.run_tenant(
            args.tenant,
            user_id=user_id,
            source_ids=tuple(sorted(requested_source_ids)) or None,
        )
        runtime_settings = runner.get_runtime(args.tenant).settings
        send_limit = runtime_settings.bot_send_limit_per_run
        candidates = await runner.latest_jobs(
            args.tenant,
            limit=publish_candidate_fetch_limit(send_limit),
            since=summary.started_at,
            user_id=user_id,
        )
        eligible = [job for job in candidates if job_passes_bot_publish_gates(job)]
        return {
            "tenant_id": args.tenant,
            "runtime": args.runtime,
            "source_count": source_count,
            "reset": reset,
            "summary": summary.as_dict(),
            "bot_filter": {
                "candidates": len(candidates),
                "eligible": len(eligible),
                "send_limit": send_limit,
            },
            "telemetry": telemetry,
        }
    finally:
        await runner.close()


def main() -> int:
    args = _build_parser().parse_args()
    _select_runtime(args.runtime)
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        if not args.preflight:
            raise
        result = {
            "tenant_id": args.tenant,
            "runtime": args.runtime,
            "preflight": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        if args.report_path is not None:
            _write_report(args.report_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2
    if args.report_path is not None:
        _write_report(args.report_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
