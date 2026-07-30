#!/usr/bin/env python3
"""Publish one eligible vacancy and verify Telegram delivery idempotency."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot

from job_ftch.adapters.telegram_bot.config import load_bot_config
from job_ftch.adapters.telegram_bot.handlers.pipeline import job_passes_bot_publish_gates
from job_ftch.adapters.telegram_bot.sender import TelegramCardSender
from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.channel_publisher import publish_jobs
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--target", required=True)
    parser.add_argument("--owner-user-id", required=True)
    parser.add_argument(
        "--configs-dir",
        type=Path,
        default=Path("job_ftch/adapters/telegram_bot/config/tenants"),
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def main() -> int:
    args = parse_args()
    settings = get_settings()
    runner = TenantRunner.from_tenants(load_tenants(args.configs_dir), base_settings=settings)
    bot: Bot | None = None
    report: dict[str, object] = {
        "run_at": datetime.now(UTC).isoformat(),
        "tenant_id": args.tenant_id,
        "target": args.target,
        "status": "INVALID",
    }
    try:
        runtime = runner.get_runtime(args.tenant_id)
        channel = await runner.get_publish_channel(args.tenant_id)
        owner = await runner.get_publish_user_id(args.tenant_id)
        if channel != args.target or owner != args.owner_user_id:
            raise RuntimeError(
                "configured Telegram target or owner does not match the requested publish"
            )
        candidates = await runner.latest_jobs(args.tenant_id, limit=50, user_id=owner)
        eligible = [job for job in candidates if job_passes_bot_publish_gates(job)]
        if not eligible:
            raise RuntimeError("no persisted job passes the bot publish gate")
        selected = max(
            eligible,
            key=lambda job: job.posted_at or job.fetched_at or datetime.min.replace(tzinfo=UTC),
        )
        auth = resolve_auth_provider(runtime.tenant.auth_provider, settings=runtime.settings)
        bot = Bot(token=load_bot_config(auth).token)
        sender = TelegramCardSender(bot)
        first = await publish_jobs(
            [selected], target=args.target, sender=sender, store=runtime.store, send_limit=1
        )
        second = await publish_jobs(
            [selected], target=args.target, sender=sender, store=runtime.store, send_limit=1
        )
        report.update(
            {
                "stable_id": selected.stable_id,
                "first_sent": first.sent,
                "second_sent": second.sent,
                "second_skipped_already_published": second.skipped_already_published,
            }
        )
        if first.sent != 1 or second.sent != 0 or second.skipped_already_published != 1:
            raise RuntimeError("publisher did not prove one-send/zero-duplicate semantics")
        report["status"] = "PASS"
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        if bot is not None:
            await bot.session.close()
        await runner.close()
        _write_report(args.out, report)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
