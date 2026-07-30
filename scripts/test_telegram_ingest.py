"""Audit configured Telegram channel sources without running the paid pipeline."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.registry import create_source_from_spec
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner, _merge_effective_sources
from job_ftch.config import get_settings
from job_ftch.domain.runtime_source import source_spec_identifier
from job_ftch.domain.source_identity import source_identity_for_raw_item
from job_ftch.domain.source_spec import TelegramChannelSpec
from job_ftch.infrastructure.classifiers.keyword_lists import (
    load_announcement_tokens,
    load_candidate_tokens,
    load_job_posting_strong_tokens,
    load_job_posting_tokens,
    load_spam_tokens,
)
from job_ftch.nodes.post_type import PostTypeClassificationNode


def _default_configs_dir() -> Path:
    return Path("job_ftch/adapters/telegram_bot/config/tenants")


def _lightweight_runner(configs_dir: Path) -> TenantRunner:
    settings = get_settings().model_copy(
        update={
            "configs_dir": configs_dir,
            "bgem3_enabled": False,
            "embedding_prefilter_enabled": False,
        }
    )
    tenants = [
        tenant.model_copy(
            update={
                "llm_backend": "heuristic",
                "embedding_enabled": False,
                "vector_backend": None,
            }
        )
        for tenant in load_tenants(configs_dir)
    ]
    return TenantRunner.from_tenants(tenants, base_settings=settings)


def _post_type_node() -> PostTypeClassificationNode:
    return PostTypeClassificationNode(
        announcement_tokens=load_announcement_tokens(),
        job_posting_tokens=load_job_posting_tokens(),
        job_posting_strong_tokens=load_job_posting_strong_tokens(),
        candidate_tokens=load_candidate_tokens(),
        spam_tokens=load_spam_tokens(),
    )


async def _probe(
    runtime: Any,
    spec: TelegramChannelSpec,
    *,
    max_messages: int,
    cutoff: datetime,
    timeout_seconds: float,
    include_full_text: bool = False,
) -> dict[str, Any]:
    bounded = spec.model_copy(update={"limit": max_messages, "freshness_cutoff_utc": cutoff})
    source = create_source_from_spec(bounded, auth=runtime.auth_provider, store=runtime.store)
    classifier = _post_type_node()
    rows: list[dict[str, Any]] = []

    async def _collect() -> None:
        async for item in source.fetch():  # type: ignore[attr-defined]
            classified = await classifier.process(item)
            identity = source_identity_for_raw_item(item)
            metadata = classified.metadata if classified is not None else item.metadata
            row = {
                "stable_id": item.stable_id,
                "url": str(item.url or ""),
                "created_at": item.created_at.isoformat(),
                "text_length": len(item.text),
                "text_preview": item.text[:300],
                "replacement_characters": item.text.count("\ufffd"),
                "post_type": metadata.get("preclassified_post_type", "unknown"),
                "post_type_model": metadata.get("preclassified_model", ""),
                "source_family": identity.family.value,
                "observation_kind": identity.observation_kind.value,
                "transport": identity.transport.value,
            }
            if include_full_text:
                row["text"] = item.text
            rows.append(row)
            if len(rows) >= max_messages:
                break

    error: str | None = None
    try:
        await asyncio.wait_for(_collect(), timeout=timeout_seconds)
    except BaseException as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    stable_ids = [row["stable_id"] for row in rows]
    urls = [row["url"] for row in rows if row["url"]]
    post_types = Counter(str(row["post_type"]) for row in rows)
    return {
        "source_id": source_spec_identifier(spec),
        "entity": spec.entity,
        "status": "ok" if rows and error is None else ("partial" if rows else "failed"),
        "error": error,
        "item_count": len(rows),
        "quality": {
            "unique_stable_ids": len(set(stable_ids)),
            "duplicate_stable_ids": len(stable_ids) - len(set(stable_ids)),
            "unique_urls": len(set(urls)),
            "duplicate_urls": len(urls) - len(set(urls)),
            "empty_text": sum(1 for row in rows if row["text_length"] == 0),
            "replacement_characters": sum(row["replacement_characters"] for row in rows),
            "newer_than_cutoff": sum(
                1 for row in rows if datetime.fromisoformat(row["created_at"]) < cutoff
            ),
            "post_types": dict(sorted(post_types.items())),
        },
        # ``sample_items`` stays bounded/readable; ``items`` is emitted only
        # for an explicit full-text manual audit.
        "sample_items": rows,
        **({"items": rows} if include_full_text else {}),
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    configs_dir = Path(args.configs_dir) if args.configs_dir else _default_configs_dir()
    runner = _lightweight_runner(configs_dir)
    runtime = runner.get_runtime(args.tenant_id)
    try:
        await runner._ensure_runtime_sources_loaded(runtime)
        effective = _merge_effective_sources(
            runtime.base_sources,
            runtime.runtime_sources,
            runtime.disabled_source_ids,
        )
        specs = [spec for spec in effective if isinstance(spec, TelegramChannelSpec)]
        cutoff = datetime.now(UTC) - timedelta(days=args.window_days)
        results = await asyncio.gather(
            *(
                _probe(
                    runtime,
                    spec,
                    max_messages=args.max_messages,
                    cutoff=cutoff,
                    timeout_seconds=args.timeout_seconds,
                    include_full_text=args.include_full_text,
                )
                for spec in specs
            )
        )
        return {
            "tenant_id": args.tenant_id,
            "window_days": args.window_days,
            "max_messages": args.max_messages,
            "source_count": len(specs),
            "summary": dict(sorted(Counter(row["status"] for row in results).items())),
            "results": sorted(results, key=lambda row: row["source_id"]),
        }
    finally:
        await runner.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--configs-dir")
    parser.add_argument("--max-messages", type=int, default=50)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--report-path", default="artifacts/debug/telegram_ingest_report.json")
    parser.add_argument(
        "--include-full-text",
        action="store_true",
        help="Persist the complete text of every extracted item for manual audit.",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    report = asyncio.run(_run(args))
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "sources": report["source_count"]}))
    print(f"Saved report to {report_path}")


if __name__ == "__main__":
    main()
