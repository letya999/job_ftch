"""Probe career-site URLs through the bounded per-source source pool.

This is the canonical entrypoint for test ingest evals over URL fixtures.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.application.registry import create_source_from_spec, resolve_site_parser
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.composite import SourceFetchResult, _capture_source_stats


class _TimedProbeSource:
    """Preserve a child source's contract while recording its terminal time."""

    def __init__(self, source: Any) -> None:
        self._source = source
        self.spec = source.spec
        self.started_at: float | None = None
        self.finished_at: float | None = None

    @property
    def stats(self) -> Any:
        return getattr(self._source, "stats", None)

    @property
    def bypass_strategy(self) -> Any:
        return getattr(self._source, "bypass_strategy", None)

    async def fetch(self) -> Any:
        self.started_at = time.monotonic()
        try:
            async for item in self._source.fetch():
                # Site parsers often emit their public domain as source_name.
                # The probe needs its temporary source identity to associate a
                # yielded item with the correct input URL after CompositeSource
                # fans sources in; production sources are never rewritten.
                if hasattr(item, "source_name") and item.source_name != self.spec.source_name:
                    item = item.model_copy(update={"source_name": self.spec.source_name})
                yield item
        finally:
            self.finished_at = time.monotonic()


def _parser_name(spec: CareerSiteSpec) -> str | None:
    parser = resolve_site_parser(spec.url)
    if parser is None:
        return None
    return parser.__class__.__name__


def _failure_bucket(
    *,
    item_count: int,
    parser_name: str | None,
    stats: dict[str, Any],
    source_result: SourceFetchResult,
) -> str | None:
    # A deadline is an operational outcome, never evidence of an empty board.
    # Check it before the generic partial branch: a zero-item source may be
    # evicted while its terminal outcome still carries an earlier zero reason.
    if source_result.deadline_exceeded and source_result.terminal_outcome not in {
        "protected",
        "blocked_or_protected",
        "no_open_vacancies",
    }:
        return "partial_with_items" if item_count else "deadline_exceeded"
    if source_result.partial:
        return source_result.terminal_outcome or "partial_with_items"
    if item_count:
        return None
    error = (source_result.error or "").casefold()
    zero_reason = str(stats.get("zero_reason") or "")
    detected = stats.get("detected_monitor_config") or {}
    if "err_tunnel_connection_failed" in error or "tunnel connection failed" in error:
        return "provider_tunnel_denied"
    if zero_reason == "soft_403_with_content":
        return "soft_403_with_content"
    if zero_reason == "waf_challenge" or (
        zero_reason == "blocked_no_bypass_left" and detected.get("challenge")
    ):
        return "waf_challenge"
    if parser_name == "ProtectedBrowserDefaultsParser" and zero_reason == "blocked_no_bypass_left":
        return "waf_challenge"
    if zero_reason == "parser_gap":
        return "parser_gap"
    if zero_reason == "provider_tunnel_denied":
        return "provider_tunnel_denied"
    if source_result.terminal_outcome == "board_gone" or zero_reason == "board_gone":
        return "stale_url"
    if source_result.terminal_outcome:
        return source_result.terminal_outcome
    if stats.get("zero_reason") == "blocked_no_bypass_left":
        return "blocked_or_protected"
    # LinkedIn is deliberately not scraped: an exhausted registered parser is
    # an access-policy outcome, not evidence that a listing was absent.
    if parser_name == "LinkedinParser":
        return "blocked_or_protected"
    if "429" in error:
        return "rate_limited"
    if "401" in error or "403" in error or "503" in error or "blocked" in error:
        return "blocked_or_protected"
    if "timeout" in error:
        return "timeout"
    if stats.get("zero_reason") == "all_scrapers_failed":
        if stats.get("detail_attempted", 0) == 0:
            return "listing_discovery_failed"
        return "detail_extraction_failed"
    if stats.get("zero_reason") == "all_monitors_exhausted":
        return (
            "detail_extraction_failed"
            if stats.get("detail_attempted", 0) > 0
            else "listing_discovery_failed"
        )
    if source_result.failed:
        return "source_error"
    # Reaching detail pages but yielding no RawItem is an extraction failure,
    # not evidence that the listing was empty.  This distinction is vital for
    # prioritising parser fixes over manually auditing genuinely empty sites.
    if stats.get("detail_attempted", 0) > 0 or stats.get("scraped", 0) > 0:
        return "detail_extraction_failed"
    if stats.get("monitored", 0) == 0 and stats.get("rich_emitted", 0) == 0:
        return "listing_discovery_failed"
    return "unconfirmed_empty"


def _probe_result(
    *,
    url: str,
    source: object,
    source_result: SourceFetchResult,
    items: list[dict[str, Any]],
    elapsed: float,
    overflow_workers_started: int,
) -> dict[str, Any]:
    raw_stats = getattr(source, "stats", {}) or {}
    stats = (
        dataclasses.asdict(raw_stats) if dataclasses.is_dataclass(raw_stats) else dict(raw_stats)
    )
    bypass_obj = getattr(source, "bypass_strategy", None)
    final_tier = getattr(bypass_obj, "current_name", None)
    route_state = getattr(bypass_obj, "route_state", None)
    final_network = getattr(getattr(route_state, "network", None), "value", None)
    escalations = int(getattr(bypass_obj, "escalations_total", 0) or 0)

    parser_name = _parser_name(source.spec)
    is_partial = bool(source_result.partial)
    return {
        "url": url,
        "parse_status": (
            "parsed_partial"
            if len(items) > 0 and is_partial
            else "parsed_ok"
            if len(items) > 0
            else "parsed_failed"
        ),
        "item_count": len(items) if len(items) > 0 else 0,
        "failure_bucket": _failure_bucket(
            item_count=len(items),
            parser_name=parser_name,
            stats=stats,
            source_result=source_result,
        ),
        "parser": parser_name,
        "elapsed_seconds": elapsed,
        "site_class": "not_probed",
        "preflight_status": None,
        "preflight_error": None,
        "exception": source_result.error,
        "bypass_final_tier": final_tier,
        "bypass_final_network": final_network,
        "bypass_escalations": escalations,
        "evicted": source_result.evicted,
        "eviction_kind": source_result.eviction_kind,
        "terminal_outcome": source_result.terminal_outcome,
        "completion_state": source_result.completion_state,
        "limited": source_result.limited,
        "deadline_exceeded": source_result.deadline_exceeded,
        "overflow_workers_started": overflow_workers_started,
        "stats": {
            k: v
            for k, v in stats.items()
            if k
            in (
                "monitored",
                "scraped",
                "rich_emitted",
                "final_monitor",
                "scrape_fallback_used",
                "zero_reason",
                "detected_monitor_config",
            )
        },
        "selection": {"site_class": "not_probed", "recommended_monitors": []},
    }


async def _probe_one(
    *,
    url: str,
    source_name: str,
    max_items: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Probe one URL with an isolated source budget.

    The ingest eval measures whether a URL can produce at least one vacancy.
    It should not depend on how many unrelated slow URLs are queued beside it.
    """
    spec = CareerSiteSpec(
        url=url,
        source_name=source_name,
        limit=max_items,
        detail_limit=max_items,
    )
    source = _TimedProbeSource(create_source_from_spec(spec))
    source_id = f"career_site:{spec.source_name}"
    source_result = SourceFetchResult(
        source_id=source_id,
        source_kind="career_site",
        source_name=spec.source_name,
    )
    items: list[dict[str, Any]] = []
    exc: BaseException | None = None
    started = time.monotonic()

    try:
        from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope

        deadline_at = asyncio.get_running_loop().time() + timeout_seconds
        async with source_deadline_scope(deadline_at):
            async for item in source.fetch():
                source_result.yielded += 1
                if len(items) < max_items:
                    items.append(
                        {
                            "url": str(getattr(item, "url", "")),
                            "text_preview": str(getattr(item, "text", ""))[:240],
                        }
                    )
                if len(items) >= max_items:
                    break
    except BaseException as err:
        exc = err
        source_result.failed = True
        err_msg = str(err).splitlines()[0] if str(err) else "Unknown"
        source_result.error = f"{err.__class__.__name__}: {err_msg}"
        if isinstance(err, TimeoutError) or "deadline exhausted" in str(err).casefold():
            source_result.evicted = True
            source_result.eviction_kind = "hard_deadline"
            source_result.deadline_exceeded = True
            source_result.hard_deadline_hit = True
            source_result.partial = bool(items)
    finally:
        _capture_source_stats(source, source_result)

    if exc is not None and items:
        source_result.failed = False
        source_result.error = source_result.error or f"{exc.__class__.__name__}: {exc}"

    return _probe_result(
        url=url,
        source=source,
        source_result=source_result,
        items=items,
        elapsed=round(time.monotonic() - started, 2),
        overflow_workers_started=0,
    )


def _timeout_result(url: str, *, elapsed_seconds: float) -> dict[str, Any]:
    return {
        "url": url,
        "parse_status": "parsed_failed",
        "item_count": 0,
        "failure_bucket": "timeout_global",
        "parser": _parser_name(CareerSiteSpec(url=url)),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "site_class": "not_probed",
        "preflight_status": None,
        "preflight_error": None,
        "exception": "TimeoutError: source task exceeded watchdog deadline",
        "bypass_final_tier": None,
        "bypass_escalations": 0,
        "evicted": True,
        "eviction_kind": "task_watchdog",
        "terminal_outcome": "deadline_exceeded",
        "completion_state": "partial",
        "limited": False,
        "deadline_exceeded": True,
        "overflow_workers_started": 0,
        "stats": {},
        "selection": {"site_class": "not_probed", "recommended_monitors": []},
    }


def _global_timeout_result(url: str, *, elapsed_seconds: float) -> dict[str, Any]:
    result = _timeout_result(url, elapsed_seconds=elapsed_seconds)
    result.update(
        {
            "failure_bucket": "global_run_timeout",
            "exception": "TimeoutError: batch global timeout elapsed before source finalized",
            "eviction_kind": "global_run_timeout",
        }
    )
    return result


def _drain_cancelled_task(task: asyncio.Task[dict[str, Any]]) -> None:
    if task.cancelled():
        return
    with contextlib.suppress(Exception):
        task.exception()


def _load_resume_results(path: Path) -> dict[str, dict[str, Any]]:
    prior_results = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prior_results, list):
        raise ValueError("--out-json must contain a JSON list to use --resume")
    return {
        str(result["url"]): result
        for result in prior_results
        if isinstance(result, dict) and isinstance(result.get("url"), str)
    }


def _load_resume_order(path: Path) -> list[str]:
    prior_results = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prior_results, list):
        raise ValueError("--out-json must contain a JSON list to use --resume")
    return [
        str(result["url"])
        for result in prior_results
        if isinstance(result, dict) and isinstance(result.get("url"), str)
    ]


def _write_slow_retry_queue(path: Path, results: list[dict[str, Any]]) -> None:
    """Persist only deadline-limited sources for a later, slower retry run."""
    urls = [
        str(result["url"])
        for result in results
        if result.get("deadline_exceeded") is True
        and (
            result.get("failure_bucket") in {"deadline_exceeded", "partial_with_items"}
            # Keep compatibility with checkpoints produced before
            # ``_failure_bucket`` classified zero-item partial deadlines.
            or result.get("terminal_outcome") in {"deadline_exceeded", "partial_with_items"}
        )
        and isinstance(result.get("url"), str)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"urls": urls}, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _gate_exit_code(results: list[dict[str, Any]], *, min_success_rate: float) -> int:
    total_count = len(results)
    if total_count == 0:
        print("GATE FAILED: no ingest results to evaluate", file=sys.stderr)
        return 1

    ok_count = sum(1 for result in results if result.get("parse_status") == "parsed_ok")
    success_rate = ok_count / total_count
    message = (
        f"ingest coverage {ok_count}/{total_count} parsed_ok "
        f"({success_rate:.2%}); required >= {min_success_rate:.2%}"
    )
    if success_rate < min_success_rate:
        print(f"GATE FAILED: {message}", file=sys.stderr)
        return 1
    print(f"GATE PASSED: {message}")
    return 0


async def main() -> int:
    import argparse

    from job_ftch.application.logging import configure_logging
    from job_ftch.config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="fixtures/real_world/career_site_ingest_110_urls.yaml")
    parser.add_argument(
        "--timeout",
        type=float,
        default=settings.source_hard_deadline_seconds,
        help="Total deadline per source; defaults to JOB_FTCH_SOURCE_HARD_DEADLINE_SECONDS.",
    )
    parser.add_argument(
        "--soft-timeout",
        type=float,
        default=settings.source_soft_deadline_seconds,
        help="Accepted for compatibility; test ingest evals use per-source hard deadlines.",
    )
    parser.add_argument(
        "--overflow-concurrency",
        type=int,
        default=settings.source_overflow_concurrency,
        help="Accepted for compatibility; isolated ingest evals do not use overflow workers.",
    )
    parser.add_argument(
        "--hard-cancel-grace",
        type=float,
        default=settings.source_hard_cancel_grace_seconds,
        help="Bounded cleanup after the hard deadline.",
    )
    parser.add_argument(
        "--global-timeout",
        type=float,
        default=None,
        help=(
            "Optional wall-clock budget for the whole batch. Unfinished and not-yet-started "
            "URLs are finalized as global_run_timeout so the result JSON is complete."
        ),
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=1,
        help="Items per URL for the parsing coverage gate; use larger values only for diagnostics.",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--start", type=int, default=0, help="Start index (inclusive)")
    parser.add_argument("--end", type=int, default=999, help="End index (exclusive)")
    parser.add_argument("--out-json", default=None, help="JSON output path for incremental saves")
    parser.add_argument(
        "--slow-queue-out",
        default=None,
        help="Write hard-deadline URLs to a YAML diagnostic queue.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip URLs already saved in --out-json and preserve their results.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero when parsed_ok / total is below --min-success-rate.",
    )
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=0.65,
        help="Minimum parsed_ok / total for --gate.",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    with open(args.input, encoding="utf-8") as f:
        urls = yaml.safe_load(f)["urls"][args.start : args.end]
    print(f"Loaded {len(urls)} URLs (indices {args.start}..{args.end - 1})")

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=3)))
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    json_path = (
        Path(args.out_json)
        if args.out_json
        else Path(f"artifacts/debug/ingest_partial_{timestamp}.json")
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)

    if args.soft_timeout >= args.timeout:
        parser.error("--soft-timeout must be smaller than --timeout")
    if args.max_items < 1:
        parser.error("--max-items must be >= 1")
    if not 0 <= args.min_success_rate <= 1:
        parser.error("--min-success-rate must be between 0 and 1")

    results_by_url: dict[str, dict[str, Any]] = {}
    resume_order: list[str] = []
    if args.resume and json_path.exists():
        try:
            results_by_url = _load_resume_results(json_path)
            resume_order = _load_resume_order(json_path)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(f"Resuming: {len(results_by_url)} saved URLs will be skipped")

    pending_urls = [url for url in urls if url not in results_by_url]
    if not pending_urls:
        if args.slow_queue_out:
            _write_slow_retry_queue(
                Path(args.slow_queue_out),
                [results_by_url[url] for url in urls if url in results_by_url],
            )
        all_results = [results_by_url[url] for url in urls if url in results_by_url]
        print(f"All {len(urls)} selected URLs are already saved in {json_path}")
        if args.gate:
            return _gate_exit_code(all_results, min_success_rate=args.min_success_rate)
        return 0
    selected_url_set = set(urls)

    def _ordered_results() -> list[dict[str, Any]]:
        preserved = [
            results_by_url[url]
            for url in resume_order
            if url in results_by_url and url not in selected_url_set
        ]
        selected = [results_by_url[url] for url in urls if url in results_by_url]
        return [*preserved, *selected]

    def _save_results() -> None:
        if results_by_url:
            json_path.write_text(
                json.dumps(_ordered_results(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    task_started_at: dict[asyncio.Task[dict[str, Any]], float] = {}
    abandoned_tasks: set[asyncio.Task[dict[str, Any]]] = set()

    async def _bounded(url: str, index: int) -> dict[str, Any]:
        task = asyncio.current_task()
        if task is not None:
            task_started_at[task] = time.monotonic()
        return await _probe_one(
            url=url,
            source_name=f"ingest_probe_{index}",
            max_items=args.max_items,
            timeout_seconds=args.timeout,
        )

    url_index = {url: index for index, url in enumerate(urls)}
    url_by_task: dict[asyncio.Task[dict[str, Any]], str] = {}

    pending_position = 0
    batch_started_at = time.monotonic()

    def _start_next_tasks() -> None:
        nonlocal pending_position
        while len(url_by_task) < args.concurrency:
            try:
                url = pending_urls[pending_position]
            except IndexError:
                return
            pending_position += 1
            task = asyncio.create_task(_bounded(url, url_index[url]), name=f"ingest:{url}")
            task.add_done_callback(_drain_cancelled_task)
            url_by_task[task] = url

    _start_next_tasks()

    def _record_result(result: dict[str, Any]) -> None:
        nonlocal completed_count
        completed_count += 1
        stall_state["ts"] = time.monotonic()  # progress signal for the watchdog
        results_by_url[str(result["url"])] = result
        _save_results()
        all_results = [results_by_url[url] for url in urls if url in results_by_url]
        ok_so_far = sum(1 for r in all_results if r["parse_status"] == "parsed_ok")
        print(f"  -> {completed_count}/{len(pending_urls)} new; {ok_so_far}/{len(all_results)} ok")

    def _abandon_overdue_task(task: asyncio.Task[dict[str, Any]]) -> dict[str, Any]:
        from job_ftch.infrastructure.sources.browser_utils import terminate_browser_descendants

        url = url_by_task.pop(task)
        task.cancel()
        abandoned_tasks.add(task)
        with contextlib.suppress(Exception):
            terminate_browser_descendants(grace=1.0)
        return _timeout_result(
            url,
            elapsed_seconds=time.monotonic() - task_started_at.get(task, time.monotonic()),
        )

    # Stall watchdog: if the event loop wedges on a hung/uncancellable browser
    # await, the asyncio.wait below never returns and no task is ever cancelled.
    # A daemon THREAD keeps running while the loop is frozen; on prolonged zero
    # progress it terminates browser descendants, which makes the wedged await
    # raise and lets the loop proceed. Only browser/driver descendants of this
    # process are killed - never the user's own Chrome.
    from job_ftch.infrastructure.sources.browser_utils import terminate_browser_descendants

    stall_state = {"ts": time.monotonic()}
    stall_stop = threading.Event()
    stall_limit = max(180.0, 3.0 * (args.timeout + args.hard_cancel_grace))

    def _stall_watchdog() -> None:
        while not stall_stop.wait(10.0):
            if time.monotonic() - stall_state["ts"] > stall_limit:
                print(
                    f"  !! stall watchdog: no progress for {stall_limit:.0f}s; "
                    "terminating browser descendants to unblock",
                    flush=True,
                )
                with contextlib.suppress(Exception):
                    terminate_browser_descendants(grace=3.0)
                stall_state["ts"] = time.monotonic()

    watchdog_thread = threading.Thread(
        target=_stall_watchdog, name="ingest-stall-watchdog", daemon=True
    )
    watchdog_thread.start()

    completed_count = 0
    def _global_budget_exhausted() -> bool:
        return args.global_timeout is not None and time.monotonic() - batch_started_at >= args.global_timeout

    while url_by_task:
        if _global_budget_exhausted():
            for task, url in list(url_by_task.items()):
                task.cancel()
                abandoned_tasks.add(task)
                _record_result(
                    _global_timeout_result(
                        url,
                        elapsed_seconds=time.monotonic() - task_started_at.get(task, batch_started_at),
                    )
                )
                url_by_task.pop(task, None)
            for url in pending_urls[pending_position:]:
                _record_result(
                    _global_timeout_result(
                        url,
                        elapsed_seconds=time.monotonic() - batch_started_at,
                    )
                )
            pending_position = len(pending_urls)
            break
        watchdog_seconds = args.timeout + args.hard_cancel_grace
        overdue = [
            task
            for task in url_by_task
            if task in task_started_at
            and time.monotonic() - task_started_at[task] >= watchdog_seconds
        ]
        if overdue:
            task = max(overdue, key=lambda candidate: time.monotonic() - task_started_at[candidate])
            result = _abandon_overdue_task(task)
        else:
            started_pending = [task for task in url_by_task if task in task_started_at]
            wait_seconds = (
                min(
                    watchdog_seconds - (time.monotonic() - task_started_at[task])
                    for task in started_pending
                )
                if started_pending
                else 1.0
            )
            done, _ = await asyncio.wait(
                set(url_by_task),
                timeout=max(0.1, wait_seconds),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                continue
            task = next(iter(done))
            url_by_task.pop(task, None)
            result = await task
        _record_result(result)
        _start_next_tasks()

    stall_stop.set()
    if abandoned_tasks:
        from job_ftch.infrastructure.sources.browser_utils import terminate_browser_descendants

        with contextlib.suppress(Exception):
            terminate_browser_descendants(grace=1.0)
        done, pending = await asyncio.wait(
            abandoned_tasks,
            timeout=max(0.1, args.hard_cancel_grace),
            return_when=asyncio.ALL_COMPLETED,
        )
        for task in done:
            _drain_cancelled_task(task)
        if pending:
            print(
                f"  !! {len(pending)} abandoned task(s) still pending after hard-cancel grace; "
                "forcing process exit after saving results",
                flush=True,
            )
            main._force_exit = True  # type: ignore[attr-defined]

    if args.slow_queue_out:
        _write_slow_retry_queue(
            Path(args.slow_queue_out),
            [results_by_url[url] for url in urls if url in results_by_url],
        )
    all_results = [results_by_url[url] for url in urls if url in results_by_url]
    ok_count = sum(1 for result in all_results if result["parse_status"] == "parsed_ok")
    print(f"  -> {len(all_results)}/{len(urls)} sources finalized | saved to {json_path}")

    ok_count = sum(1 for r in all_results if r["parse_status"] == "parsed_ok")
    fail_count = sum(1 for r in all_results if r["parse_status"] == "parsed_failed")
    print(f"\nResults: {ok_count} parsed_ok, {fail_count} parsed_failed, {len(all_results)} total")
    if args.gate:
        return _gate_exit_code(all_results, min_success_rate=args.min_success_rate)
    return 0


if __name__ == "__main__":
    import os

    from job_ftch.infrastructure.sources.browser_utils import terminate_browser_descendants

    _exit_code = 0
    try:
        _exit_code = asyncio.run(main())
    finally:
        # Force-terminate browser/driver descendants this run spawned so the
        # interpreter can exit; only descendants of THIS process are touched,
        # never the user's own Chrome. See browser_utils for the shared helper.
        terminate_browser_descendants()
        if getattr(main, "_force_exit", False):
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(_exit_code)
    raise SystemExit(_exit_code)
