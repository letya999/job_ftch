"""job_ftch composition root for local phase-0 runs."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

import httpx
import structlog

from application.context import ProcessingContext
from application.logging import configure_logging
from application.pipeline import Pipeline
from application.telemetry import configure_telemetry
from config import Settings, SinkBackend, SourceBackend, StoreBackend
from infrastructure.sources import (
    CareerSiteSource,
    LocalFixtureSource,
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
)
from infrastructure.stores.in_memory import InMemoryStore
from infrastructure.stores.postgres import PostgresStore
from nodes import OriginPolicyNode, SanitizeNode, ValidateRawNode
from sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from application.contracts import Node, Sink, Source, Store
    from application.run_summary import RunSummary
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
        updates["debug_source_path"] = args.source_path
    if args.telegram_entity is not None:
        updates["telegram_entity"] = args.telegram_entity
    if args.career_site_url is not None:
        updates["career_site_url"] = args.career_site_url
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
    if settings.source_backend in {
        SourceBackend.TELEGRAM_CHANNEL,
        SourceBackend.TELEGRAM_GROUP,
        SourceBackend.TELEGRAM_COMMENT,
    }:
        if settings.telegram_api_id is None or settings.telegram_api_hash is None:
            msg = (
                "Telegram sources require JOB_FTCH_TELEGRAM_API_ID and JOB_FTCH_TELEGRAM_API_HASH."
            )
            raise ValueError(msg)
        if settings.telegram_entity is None:
            msg = "Telegram sources require JOB_FTCH_TELEGRAM_ENTITY."
            raise ValueError(msg)
        from telethon import TelegramClient

        settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(
            str(settings.telegram_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        client.flood_sleep_threshold = settings.telegram_flood_sleep_threshold_seconds
        if settings.source_backend is SourceBackend.TELEGRAM_CHANNEL:
            return TelegramChannelSource(
                client,
                settings.telegram_entity,
                limit=settings.telegram_message_limit,
                wait_time=settings.telegram_history_wait_time_seconds,
                own_client=True,
            )
        if settings.source_backend is SourceBackend.TELEGRAM_GROUP:
            return TelegramGroupSource(
                client,
                settings.telegram_entity,
                limit=settings.telegram_message_limit,
                wait_time=settings.telegram_history_wait_time_seconds,
                own_client=True,
            )
        return TelegramCommentSource(
            client,
            settings.telegram_entity,
            post_limit=settings.telegram_comment_post_limit,
            comment_limit_per_post=settings.telegram_comment_limit_per_post,
            wait_time=settings.telegram_history_wait_time_seconds,
            own_client=True,
        )
    if settings.source_backend is SourceBackend.CAREER_SITE:
        if settings.career_site_url is None:
            msg = "Career site source requires JOB_FTCH_CAREER_SITE_URL."
            raise ValueError(msg)
        client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        return CareerSiteSource(
            client,
            settings.career_site_url,
            limit=settings.pipeline_max_items_per_run,
            own_client=True,
        )
    msg = f"Unsupported source backend: {settings.source_backend}"
    raise ValueError(msg)


def build_nodes(settings: Settings) -> list[Node[RawItem, RawItem]]:
    return [
        SanitizeNode(),
        ValidateRawNode(),
        OriginPolicyNode(allowed_career_site_hosts=settings.career_site_allowed_hosts),
    ]


def build_sink(settings: Settings) -> Sink[RawItem]:
    if settings.sink_backend is SinkBackend.JSON_FILE:
        return JsonFileSink(settings.output_path, jsonl=settings.output_jsonl)
    msg = f"Unsupported sink backend: {settings.sink_backend}"
    raise ValueError(msg)


def build_quarantine_sink(settings: Settings) -> Sink[QuarantinedRawItem]:
    if settings.sink_backend is SinkBackend.JSON_FILE:
        return JsonFileSink(
            settings.quarantine_output_path,
            jsonl=settings.quarantine_output_jsonl,
        )
    msg = f"Unsupported sink backend: {settings.sink_backend}"
    raise ValueError(msg)


def build_store(settings: Settings) -> Store:
    if settings.store_backend is StoreBackend.MEMORY:
        return InMemoryStore()
    if settings.store_backend is StoreBackend.POSTGRES:
        if settings.postgres_dsn is None:
            msg = "PostgreSQL store backend requires JOB_FTCH_POSTGRES_DSN."
            raise ValueError(msg)
        return PostgresStore(settings.postgres_dsn)
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
        nodes=build_nodes(settings),
        sink=build_sink(settings),
        store=build_store(settings),
        quarantine_sink=build_quarantine_sink(settings),
    )
    summary = await pipeline.run(
        max_items=settings.pipeline_max_items_per_run,
        context=ProcessingContext(
            dry_run=settings.dry_run,
            max_text_length=settings.max_text_length,
            allowed_domains=frozenset(settings.career_site_allowed_hosts),
            dedup_threshold=settings.dedup_threshold,
        ),
    )
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
