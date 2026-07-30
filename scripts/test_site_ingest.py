"""Run direct ingest checks for career-site sources without the pipeline.

This script resolves the tenant's base + runtime career-site sources, applies
bounded limits, fetches up to N raw items from each source, and records why
ingest failed when a source yields nothing or raises.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import io
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.registry import (
    create_source_from_spec,
    resolve_bypass,
    resolve_site_parser,
)
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner, _merge_effective_sources
from job_ftch.config import Settings, get_settings
from job_ftch.domain.runtime_source import source_spec_identifier
from job_ftch.domain.source_identity import source_identity_for_raw_item
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.classifiers.keyword_lists import (
    load_announcement_tokens,
    load_candidate_tokens,
    load_job_posting_strong_tokens,
    load_job_posting_tokens,
    load_spam_tokens,
)
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint
from job_ftch.nodes.post_type import PostTypeClassificationNode


def _default_configs_dir() -> Path:
    return Path("job_ftch/adapters/telegram_bot/config/tenants")


def _build_settings(configs_dir: Path) -> Settings:
    base = get_settings()
    return base.model_copy(update={"configs_dir": configs_dir})


def _site_kind(spec: CareerSiteSpec) -> str:
    parser = resolve_site_parser(spec.url)
    if parser is not None and getattr(parser, "has_custom_parse", False):
        return "custom_parser"
    if spec.monitor_config.get("render") or spec.scraper_config.get("render"):
        return "rendered_spa"
    if spec.monitor == "api_sniffer":
        return "api_discovery"
    return "generic_html"


def _parser_name(spec: CareerSiteSpec) -> str | None:
    parser = resolve_site_parser(spec.url)
    if parser is None:
        return None
    return str(parser.__class__.__name__)


def _bounded_spec(
    spec: CareerSiteSpec,
    max_items: int,
    *,
    window_days: int | None = None,
) -> CareerSiteSpec:
    limit = min(spec.limit or max_items, max_items)
    detail_limit = min(spec.detail_limit or max_items, max_items)
    return spec.model_copy(
        update={
            "limit": limit,
            "detail_limit": detail_limit,
            "freshness_cutoff_utc": (
                datetime.now(UTC) - timedelta(days=window_days) if window_days is not None else None
            ),
        }
    )


def _stringify_exception(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"


async def _preflight(spec: CareerSiteSpec) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(
                spec.url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        title = ""
        match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
        if match:
            title = " ".join(match.group(1).split())[:160]
        return {
            "status_code": response.status_code,
            "final_url": str(response.url),
            "title": title,
            "body_preview": response.text[:240],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status_code": None,
            "final_url": spec.url,
            "title": "",
            "body_preview": "",
            "error": _stringify_exception(exc),
        }


async def _selection_diagnostic(source: Any, spec: CareerSiteSpec) -> dict[str, Any]:
    bypass = resolve_bypass(spec.bypass or "auto", spec.bypass_config)
    applied_http = source.http
    with contextlib.suppress(Exception):
        applied_http = await bypass.apply_http(source.http)

    profile_data: dict[str, Any] = {
        "site_class": "unknown",
        "recommended_monitors": [],
        "detected_config": {},
        "available_tiers": list(getattr(bypass, "available_tiers", ()) or ()),
        "initial_tier": getattr(bypass, "current_name", None),
    }
    try:
        async with client_for_config(applied_http, spec.monitor_config) as probe_http:
            profile = await fingerprint(spec.url, probe_http)
            ordered_monitors, detected_config, _canonical = await get_ordered_monitors(
                spec.url, probe_http
            )
    except Exception as exc:  # noqa: BLE001
        profile_data["probe_error"] = _stringify_exception(exc)
        return profile_data

    profile_data["site_class"] = str(profile.site_class)
    profile_data["recommended_monitors"] = list(ordered_monitors)
    profile_data["detected_config"] = dict(detected_config)
    return profile_data


def _failure_bucket(
    *,
    item_count: int,
    exception_text: str | None,
    preflight: dict[str, Any],
    stats: dict[str, Any],
    final_tier: str | None,
    escalations_total: int,
) -> str | None:
    if exception_text:
        lowered = exception_text.casefold()
        if "429" in lowered:
            return "rate_limited"
        if "403" in lowered or "503" in lowered or "blocked" in lowered:
            return "blocked_or_protected"
        if "timeout" in lowered:
            return "timeout"
        return "source_error"
    if item_count > 0:
        return None
    status_code = preflight.get("status_code")
    final_url = str(preflight.get("final_url") or "").casefold()
    title = str(preflight.get("title") or "").casefold()
    body_preview = str(preflight.get("body_preview") or "").casefold()
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code in {401, 403, 503}:
        return "blocked_or_protected"
    if "challenge.html" in final_url and "429" in final_url:
        return "rate_limited"
    if "429" in title or "too many requests" in title:
        return "rate_limited"
    if "ошибка 429" in body_preview or "too many requests" in body_preview:
        return "rate_limited"
    if "antibot challenge" in title or "too many requests" in body_preview:
        return "blocked_or_protected"
    if escalations_total > 0 or final_tier in {"stealth_browser", "cloak"}:
        if stats.get("monitored", 0) > 0 and stats.get("scraped", 0) == 0:
            return "captcha_or_antibot_on_detail"
        return "captcha_or_antibot_on_listing"
    if stats.get("monitored", 0) > 0 and stats.get("scraped", 0) == 0:
        return "detail_extraction_failed"
    if stats.get("monitored", 0) == 0 and stats.get("rich_emitted", 0) == 0:
        return "listing_discovery_failed"
    return "empty_result"


async def _probe_site_source(
    runtime: Any,
    spec: CareerSiteSpec,
    *,
    max_items: int,
    timeout_seconds: float,
    window_days: int | None = None,
    include_full_text: bool = False,
) -> dict[str, Any]:
    bounded = _bounded_spec(spec, max_items, window_days=window_days)
    source = create_source_from_spec(bounded, auth=runtime.auth_provider, store=runtime.store)
    source_id = source_spec_identifier(spec)
    diagnostic_timeout = min(20.0, max(5.0, timeout_seconds / 3))
    try:
        preflight = await asyncio.wait_for(_preflight(spec), timeout=diagnostic_timeout)
    except TimeoutError:
        preflight = {
            "status_code": None,
            "final_url": spec.url,
            "title": "",
            "body_preview": "",
            "error": f"TimeoutError: preflight exceeded {diagnostic_timeout:.1f}s",
        }
    try:
        selection = await asyncio.wait_for(
            _selection_diagnostic(source, spec),
            timeout=diagnostic_timeout,
        )
    except TimeoutError:
        selection = {
            "site_class": "unknown",
            "recommended_monitors": [],
            "detected_config": {},
            "probe_error": f"TimeoutError: selection exceeded {diagnostic_timeout:.1f}s",
        }
    started = time.monotonic()
    items: list[dict[str, Any]] = []
    exc: BaseException | None = None
    classifier = PostTypeClassificationNode(
        announcement_tokens=load_announcement_tokens(),
        job_posting_tokens=load_job_posting_tokens(),
        job_posting_strong_tokens=load_job_posting_strong_tokens(),
        candidate_tokens=load_candidate_tokens(),
        spam_tokens=load_spam_tokens(),
    )

    async def _collect() -> None:
        async for item in source.fetch():  # type: ignore[attr-defined]
            metadata = getattr(item, "metadata", {}) or {}
            identity = source_identity_for_raw_item(item)
            classified = await classifier.process(item)
            classified_metadata = classified.metadata if classified is not None else metadata
            text = str(getattr(item, "text", ""))
            row = {
                "stable_id": str(getattr(item, "stable_id", "")),
                "url": str(getattr(item, "url", "")),
                "source_name": str(getattr(item, "source_name", source_id)),
                "text_preview": text[:240],
                "text_length": len(text),
                "replacement_characters": text.count("\ufffd"),
                "created_at": str(getattr(item, "created_at", "") or ""),
                "date_posted": str(metadata.get("date_posted") or ""),
                "detail_vacancy_confirmed": metadata.get("detail_vacancy_confirmed") is True,
                "post_type": classified_metadata.get("preclassified_post_type", "unknown"),
                "post_type_model": classified_metadata.get("preclassified_model", ""),
                "source_family": identity.family.value,
                "observation_kind": identity.observation_kind.value,
                "transport": identity.transport.value,
            }
            if include_full_text:
                row["text"] = text
            items.append(row)
            if len(items) >= max_items:
                break

    try:
        await asyncio.wait_for(_collect(), timeout=timeout_seconds)
    except BaseException as err:  # noqa: BLE001
        exc = err

    elapsed = round(time.monotonic() - started, 2)
    raw_stats = getattr(source, "stats", {}) or {}
    stats = (
        dataclasses.asdict(cast("Any", raw_stats))
        if dataclasses.is_dataclass(raw_stats)
        else dict(raw_stats)
    )
    bypass = getattr(source, "bypass_strategy", None)
    final_tier = getattr(bypass, "current_name", None)
    escalations_total = int(getattr(bypass, "escalations_total", 0) or 0)
    exception_text = _stringify_exception(exc)
    bucket = _failure_bucket(
        item_count=len(items),
        exception_text=exception_text,
        preflight=preflight,
        stats=stats,
        final_tier=final_tier,
        escalations_total=escalations_total,
    )
    stable_ids = [item["stable_id"] for item in items if item["stable_id"]]
    urls = [item["url"] for item in items if item["url"]]
    quality = {
        "unique_stable_ids": len(set(stable_ids)),
        "duplicate_stable_ids": len(stable_ids) - len(set(stable_ids)),
        "unique_urls": len(set(urls)),
        "duplicate_urls": len(urls) - len(set(urls)),
        "detail_vacancy_confirmed": sum(1 for item in items if item["detail_vacancy_confirmed"]),
        "undated": sum(1 for item in items if not item["created_at"] and not item["date_posted"]),
        "empty_text": sum(1 for item in items if item["text_length"] == 0),
        "replacement_characters": sum(item["replacement_characters"] for item in items),
        "post_types": dict(sorted(Counter(item["post_type"] for item in items).items())),
        "source_families": dict(sorted(Counter(item["source_family"] for item in items).items())),
        "observation_kinds": dict(
            sorted(Counter(item["observation_kind"] for item in items).items())
        ),
    }

    return {
        "source_id": source_id,
        "source_name": spec.source_name or source_id,
        "url": spec.url,
        "site_kind": _site_kind(spec),
        "parser": _parser_name(spec),
        "monitor": spec.monitor,
        "monitor_config": spec.monitor_config,
        "scraper": spec.scraper,
        "scraper_config": spec.scraper_config,
        "configured_limit": spec.limit,
        "configured_detail_limit": spec.detail_limit,
        "effective_limit": bounded.limit,
        "effective_detail_limit": bounded.detail_limit,
        "selection": selection,
        "item_count": len(items),
        "quality": quality,
        "sample_items": items[:3],
        **({"items": items} if include_full_text else {}),
        "elapsed_seconds": elapsed,
        "status": "partial" if items and bucket else ("ok" if items else "failed"),
        "failure_bucket": bucket,
        "exception": exception_text,
        "preflight": preflight,
        "stats": stats,
        "site_class": selection["site_class"],
        "recommended_monitors": selection["recommended_monitors"],
        "detected_monitor_config": selection["detected_config"],
        "monitor_attempts": list(stats.get("monitor_attempts", []) or []),
        "final_monitor": stats.get("final_monitor"),
        "bypass_final_tier": final_tier,
        "bypass_escalations_total": escalations_total,
        "available_tiers": list(getattr(bypass, "available_tiers", ()) or ()),
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    configs_dir = Path(args.configs_dir) if args.configs_dir else _default_configs_dir()
    settings = _build_settings(configs_dir).model_copy(
        update={"bgem3_enabled": False, "embedding_prefilter_enabled": False}
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
    runner = TenantRunner.from_tenants(tenants, base_settings=settings)
    runtime = runner.get_runtime(args.tenant_id)
    await runner._ensure_runtime_sources_loaded(runtime)

    effective_sources = _merge_effective_sources(
        runtime.base_sources,
        runtime.runtime_sources,
        runtime.disabled_source_ids,
    )
    site_specs = [spec for spec in effective_sources if isinstance(spec, CareerSiteSpec)]
    if args.only:
        selected = set(args.only)
        site_specs = [
            spec
            for spec in site_specs
            if source_spec_identifier(spec) in selected
            or (spec.source_name or "") in selected
            or spec.url in selected
        ]

    semaphore = asyncio.Semaphore(max(args.concurrency, 1))

    async def _one(spec: CareerSiteSpec) -> dict[str, Any]:
        async with semaphore:
            return await _probe_site_source(
                runtime,
                spec,
                max_items=args.max_items,
                timeout_seconds=args.timeout_seconds,
                window_days=args.window_days,
                include_full_text=args.include_full_text,
            )

    results = await asyncio.gather(*(_one(spec) for spec in site_specs))

    by_bucket = Counter(item["failure_bucket"] or "ok" for item in results)
    failures_by_kind = Counter(
        f"{item['site_kind']}:{item['failure_bucket']}"
        for item in results
        if item["failure_bucket"] is not None
    )
    by_site_class = Counter(item["site_class"] for item in results)
    by_final_monitor = Counter(str(item["final_monitor"] or "none") for item in results)

    return {
        "tenant_id": args.tenant_id,
        "configs_dir": str(configs_dir),
        "max_items": args.max_items,
        "timeout_seconds": args.timeout_seconds,
        "concurrency": args.concurrency,
        "window_days": args.window_days,
        "site_count": len(site_specs),
        "summary": {
            "ok": sum(1 for item in results if item["status"] == "ok"),
            "partial": sum(1 for item in results if item["status"] == "partial"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
            "by_bucket": dict(sorted(by_bucket.items())),
            "by_site_kind_and_bucket": dict(sorted(failures_by_kind.items())),
            "by_site_class": dict(sorted(by_site_class.items())),
            "by_final_monitor": dict(sorted(by_final_monitor.items())),
        },
        "results": sorted(results, key=lambda item: item["source_id"]),
    }


def _print_report(report: dict[str, Any]) -> None:
    print(
        f"Career-site ingest report for tenant={report['tenant_id']} "
        f"(sites={report['site_count']}, limit={report['max_items']})"
    )
    print(
        f"{'SOURCE':<30} {'CLASS':<10} {'MONITOR':<16} {'STATUS':<8} {'ITEMS':>5} "
        f"{'SEC':>6} {'TIER':<16} REASON"
    )
    print("-" * 138)
    for item in report["results"]:
        reason = item["failure_bucket"] or ""
        print(
            f"{item['source_name'][:30]:<30} {item['site_class']:<10} "
            f"{str(item['final_monitor'] or '')[:16]:<16} {item['status']:<8} {item['item_count']:>5} "
            f"{item['elapsed_seconds']:>6.1f} {str(item['bypass_final_tier'] or ''):<16} "
            f"{reason}"
        )
    print("\nSummary")
    for key, value in report["summary"]["by_bucket"].items():
        print(f"{key}: {value}")
    print("\nBy site class")
    for key, value in report["summary"]["by_site_class"].items():
        print(f"{key}: {value}")
    print("\nBy final monitor")
    for key, value in report["summary"]["by_final_monitor"].items():
        print(f"{key}: {value}")


def main() -> None:
    from job_ftch.application.logging import configure_logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--configs-dir")
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--window-days", type=int)
    parser.add_argument("--report-path", default="artifacts/debug/site_ingest_report.json")
    parser.add_argument(
        "--include-full-text",
        action="store_true",
        help="Persist the complete text of every extracted item for manual audit.",
    )
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    configure_logging("INFO")
    report = asyncio.run(_run(args))
    _print_report(report)
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved report to {report_path}")


if __name__ == "__main__":
    main()
