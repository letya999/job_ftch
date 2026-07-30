from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, cast

import asyncpg
from pydantic import TypeAdapter

from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import get_settings
from job_ftch.domain.source_spec import SourceSpec

DEFAULT_TENANT_ID = "ai_jobs"
DEFAULT_USER_ID = "480637186"
DEFAULT_FIXTURE = Path("fixtures/sources/ai_jobs.json")
DEFAULT_CONFIGS_DIR = Path("job_ftch/adapters/telegram_bot/config/tenants")
_SOURCE_SPEC_ADAPTER = TypeAdapter(SourceSpec)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset tenant runtime sources, import fixture/URLs, and probe them.",
    )
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Inline source input such as a URL, @channel, t.me/name, or type:value.",
    )
    parser.add_argument(
        "--source-type",
        default=None,
        help="Optional explicit type for every --source value (career_site/rss_feed/etc).",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--probe-max-items", type=int, default=5)
    parser.add_argument("--skip-probe", action="store_true")
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    return parser.parse_args()


async def _cleanup_source_state(dsn: str, tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "DELETE FROM jf_source_snapshots WHERE tenant_id = $1",
            tenant_id,
        )
        await conn.execute(
            "DELETE FROM jf_source_ingest_state WHERE tenant_id = $1",
            tenant_id,
        )
        await conn.execute(
            "DELETE FROM jf_source_assessments WHERE tenant_id = $1",
            tenant_id,
        )
        await conn.execute(
            "DELETE FROM jf_kv WHERE key LIKE ANY($1::text[])",
            (
                f"{tenant_id}:runtime_source:%",
                f"{tenant_id}:source_disabled:%",
                f"{tenant_id}:source_health:%",
            ),
        )
        await conn.execute(
            "DELETE FROM jf_set WHERE key = ANY($1::text[])",
            (
                f"{tenant_id}:runtime_source_ids",
                f"{tenant_id}:source_disabled_ids",
                f"{tenant_id}:source_health_ids",
            ),
        )
    finally:
        await conn.close()


def _load_fixture_specs(path: Path) -> list[SourceSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources")
    if not isinstance(rows, list):
        raise ValueError(f"Expected 'sources' list in {path}")
    return [cast("SourceSpec", _SOURCE_SPEC_ADAPTER.validate_python(item)) for item in rows]


async def _build_inline_specs(
    values: list[str],
    *,
    source_type: str | None,
    limit: int,
) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    for value in values:
        specs.append(
            await build_source_spec_from_input(
                value,
                source_type=source_type,
                limit=limit,
            )
        )
    return specs


async def main() -> int:
    args = parse_args()
    settings = get_settings()
    if settings.store_dsn is None:
        raise RuntimeError("store_dsn is required")
    dsn = settings.store_dsn.get_secret_value()

    fixture_specs = _load_fixture_specs(args.fixture)
    inline_specs = await _build_inline_specs(
        args.source,
        source_type=args.source_type,
        limit=args.limit,
    )
    specs = fixture_specs + inline_specs
    if not specs:
        raise RuntimeError("No sources to import")

    tenants = load_tenants(args.configs_dir)
    runner = TenantRunner.from_tenants(tenants, base_settings=settings)
    try:
        await runner.clear_sources(args.tenant_id)
        await _cleanup_source_state(dsn, args.tenant_id)

        imported: list[dict[str, Any]] = []
        for spec in specs:
            payload = await runner.add_source_spec(
                args.tenant_id,
                spec,
                added_via="script:reset_ai_jobs_sources",
                added_by=args.user_id,
                input_value=getattr(spec, "url", None) or getattr(spec, "entity", None),
            )
            imported.append(payload)

        if not args.skip_probe:
            for payload in imported:
                await runner.run_tenant(
                    args.tenant_id,
                    user_id=args.user_id,
                    source_ids=[payload["source_id"]],
                    max_items=args.probe_max_items,
                )

        final_sources = await runner.list_sources(args.tenant_id)
        summary = {
            "tenant_id": args.tenant_id,
            "imported": len(imported),
            "listed": len(final_sources),
            "source_ids": [item["source_id"] for item in final_sources],
            "status_counts": {
                status: sum(1 for item in final_sources if item.get("status") == status)
                for status in sorted({str(item.get("status")) for item in final_sources})
            },
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        await runner.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
