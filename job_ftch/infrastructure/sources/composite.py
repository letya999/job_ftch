"""CompositeSource: fan-in over multiple Source adapters."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.domain import SourceOutcome, source_spec_identifier, source_spec_name

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from job_ftch.application.contracts import Source
    from job_ftch.domain import QuarantinedRawItem, RawItem

logger = logging.getLogger("job_ftch.composite_source")


@dataclass(slots=True)
class SourceFetchResult:
    source_id: str
    source_kind: str
    source_name: str
    yielded: int = 0
    failed: bool = False
    error: str | None = None
    evicted: bool = False
    eviction_kind: str | None = None
    partial: bool = False
    zero_reason: str | None = None
    monitored: int = 0
    rich_emitted: int = 0
    scraped: int = 0
    scrape_fallback_used: int = 0
    browser_navigations_attempted: int = 0
    monitor_truncated: int = 0
    freshness_filtered: int = 0
    freshness_undated_passed: int = 0
    parser_duplicates_suppressed: int = 0
    terminal_outcome: SourceOutcome | None = None
    deadline_exceeded: bool = False
    soft_deadline_hit: bool = False
    hard_deadline_hit: bool = False
    limited: bool = False
    completion_state: str = "pending"
    requested_parser: str | None = None
    actual_parser: str | None = None
    fallback_chain: list[str] | None = None
    generic_monitor_used: bool = False
    generic_scraper_used: bool = False
    parser_urls_discovered: int = 0
    detail_cards_extracted: int = 0


_TECHNICAL_ZERO_REASONS = {
    "all_monitors_exhausted",
    "blocked_no_bypass_left",
    "all_scrapers_failed",
    "waf_challenge",
    "provider_tunnel_denied",
    "soft_403_with_content",
    "stale_url",
    "parser_gap",
}

_OUTCOME_BY_ZERO_REASON = {
    "blocked_no_bypass_left": "protected",
    "waf_challenge": "waf_challenge",
    "provider_tunnel_denied": "provider_tunnel_denied",
    "soft_403_with_content": "soft_403_with_content",
    "stale_url": "stale_url",
    "parser_gap": "parser_gap",
    "all_scrapers_failed": "detail_extraction_failed",
    "all_monitors_exhausted": "listing_discovery_failed",
    "monitor_empty": "unconfirmed_empty",
    "confirmed_empty": "no_open_vacancies",
    "board_gone": "board_gone",
    "rate_limited": "rate_limited",
}


def _capture_source_stats(source: object, result: SourceFetchResult) -> None:
    """Copy adapter-owned health into the cross-source result contract."""
    stats = getattr(source, "stats", None)
    if stats is not None:
        result.yielded = getattr(stats, "yielded", result.yielded)
        # A source interrupted while emitting is partial.  A configured
        # frontier cap is successful but limited; merging them made every
        # ``--max-items`` probe look like a timeout failure.
        result.partial = result.partial or bool(getattr(stats, "source_partial", False))
        result.limited = result.limited or bool(
            getattr(stats, "truncated", False) or getattr(stats, "monitor_truncated", 0)
        )
        if getattr(stats, "rate_limited", False):
            result.partial = True
        raw_reason = getattr(stats, "zero_reason", None)
        result.zero_reason = str(getattr(raw_reason, "value", raw_reason)) if raw_reason else None
        for name in (
            "monitored",
            "rich_emitted",
            "scraped",
            "scrape_fallback_used",
            "browser_navigations_attempted",
            "monitor_truncated",
            "freshness_filtered",
            "freshness_undated_passed",
            "parser_duplicates_suppressed",
        ):
            setattr(result, name, int(getattr(stats, name, 0) or 0))
        result.requested_parser = getattr(stats, "requested_parser", None)
        result.actual_parser = getattr(stats, "actual_parser", None)
        result.fallback_chain = list(getattr(stats, "fallback_chain", ()) or ())
        result.generic_monitor_used = bool(getattr(stats, "monitor_attempts", ()))
        result.generic_scraper_used = bool(getattr(stats, "scrape_fallback_used", 0))
        result.parser_urls_discovered = int(getattr(stats, "parser_urls_discovered", 0) or 0)
        result.detail_cards_extracted = int(getattr(stats, "detail_cards_extracted", 0) or 0)
        if result.yielded == 0 and result.zero_reason in _TECHNICAL_ZERO_REASONS:
            result.failed = True
            result.error = result.error or f"source_zero_yield:{result.zero_reason}"
    if result.yielded:
        if result.partial or result.deadline_exceeded:
            result.terminal_outcome = "partial_with_items"
            result.completion_state = "partial"
        else:
            result.terminal_outcome = "parsed_ok"
            result.completion_state = "completed_limited" if result.limited else "completed"
    elif result.zero_reason:
        result.terminal_outcome = cast(
            "SourceOutcome | None", _OUTCOME_BY_ZERO_REASON.get(result.zero_reason)
        )
        # Keep the specific diagnostic outcome (for example failed detail
        # extraction) but never treat a hard-deadline run as a complete source
        # snapshot merely because it reached a zero-yield branch while timing
        # out.
        result.completion_state = (
            "partial" if (result.deadline_exceeded or result.partial) else "completed"
        )
    elif result.deadline_exceeded:
        result.terminal_outcome = result.terminal_outcome or "deadline_exceeded"
        result.completion_state = "partial"
    elif result.failed:
        result.terminal_outcome = "failed"
        result.completion_state = "failed"
    elif result.completion_state == "pending":
        result.completion_state = "completed"


@dataclass(slots=True)
class _SourceStreamState:
    source: Source[RawItem]
    result: SourceFetchResult
    queue: asyncio.Queue[object]
    producer: asyncio.Task[None]
    started_at: float
    deadline_at: float


_QUEUE_DONE = object()
_QUEUE_ERROR = object()


def _source_identity(source: object) -> tuple[str, str, str]:
    spec = getattr(source, "spec", None)
    if spec is not None:
        source_id = source_spec_identifier(spec)
        source_kind, _, source_name = source_id.partition(":")
        return source_id, source_kind, source_name or source_spec_name(spec)
    source_kind = str(getattr(source, "source_kind", "") or type(source).__name__)
    source_name = str(
        getattr(source, "source_name", "")
        or getattr(source, "_channel", "")
        or getattr(source, "_group", "")
        or getattr(source, "_entity", "")
        or getattr(source, "_fixture_path", "")
        or repr(source)
    )
    return f"{source_kind}:{source_name}", source_kind, source_name


class CompositeSource:
    """Fan-in source that aggregates items from multiple child sources.

    Sequential mode (concurrency=1): yields items from each child in order.
    Parallel mode (concurrency>1): uses asyncio.TaskGroup + bounded Queue.
    A failing child records an error and does not abort others.
    """

    def __init__(
        self,
        sources: Sequence[Source[RawItem]],
        *,
        concurrency: int = 1,
        queue_capacity: int = 100,
        dynamic_enabled: bool = False,
        soft_deadline_seconds: float | None = None,
        hard_deadline_seconds: float | None = None,
        overflow_concurrency: int | None = None,
        hard_cancel_grace_seconds: float | None = None,
        adaptive_resize: bool = False,
        concurrency_max: int | None = None,
    ) -> None:
        from job_ftch.config import get_settings

        settings = get_settings()
        soft_deadline_seconds = (
            settings.source_soft_deadline_seconds
            if soft_deadline_seconds is None
            else soft_deadline_seconds
        )
        hard_deadline_seconds = (
            settings.source_hard_deadline_seconds
            if hard_deadline_seconds is None
            else hard_deadline_seconds
        )
        overflow_concurrency = (
            settings.source_overflow_concurrency
            if overflow_concurrency is None
            else overflow_concurrency
        )
        hard_cancel_grace_seconds = (
            settings.source_hard_cancel_grace_seconds
            if hard_cancel_grace_seconds is None
            else hard_cancel_grace_seconds
        )
        if not sources:
            raise ValueError("CompositeSource requires at least one child source.")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1.")
        if soft_deadline_seconds >= hard_deadline_seconds:
            raise ValueError("soft_deadline_seconds must be smaller than hard_deadline_seconds.")
        if hard_cancel_grace_seconds < 0:
            raise ValueError("hard_cancel_grace_seconds must be >= 0.")
        self._sources = list(sources)
        self._concurrency = concurrency
        self._queue_capacity = queue_capacity
        self._dynamic_enabled = dynamic_enabled
        self._soft_deadline_seconds = soft_deadline_seconds
        self._hard_deadline_seconds = hard_deadline_seconds
        self._overflow_concurrency = overflow_concurrency
        self._hard_cancel_grace_seconds = hard_cancel_grace_seconds
        self._adaptive_resize = adaptive_resize
        self._concurrency_max = max(concurrency_max or concurrency, concurrency)
        self.failed_sources: int = 0
        self.overflow_workers_started: int = 0
        self.source_results: dict[str, SourceFetchResult] = {}

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        self.failed_sources = 0
        self.source_results = {}
        if self._concurrency == 1:
            async for item in self._fetch_sequential():
                yield item
        elif self._dynamic_enabled:
            async for item in self._fetch_parallel_dynamic():
                yield item
        else:
            async for item in self._fetch_parallel():
                yield item

    async def _fetch_sequential(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        for source in self._sources:
            from job_ftch.infrastructure.sources.source_deadline import (
                reset_source_deadline,
                set_source_deadline,
            )

            source_id, source_kind, source_name = _source_identity(source)
            context_tokens = bind_contextvars(
                source_id=source_id,
                source_kind=source_kind,
            )
            result = self.source_results.setdefault(
                source_id,
                SourceFetchResult(
                    source_id=source_id,
                    source_kind=source_kind,
                    source_name=source_name,
                ),
            )
            failed_before = result.failed
            deadline_token = set_source_deadline(
                asyncio.get_running_loop().time() + self._hard_deadline_seconds
            )
            try:
                async with asyncio.timeout(self._hard_deadline_seconds):
                    async for item in source.fetch():
                        result.yielded += 1
                        yield item
            except TimeoutError:
                result.evicted = True
                result.eviction_kind = "hard_deadline"
                result.deadline_exceeded = True
                result.hard_deadline_hit = True
                result.partial = result.yielded > 0
                result.failed = True
                result.error = "source_hard_deadline_exceeded"
            except Exception as exc:
                result.failed = True
                err_msg = str(exc).splitlines()[0] if str(exc) else "Unknown"
                result.error = f"{exc.__class__.__name__}: {err_msg}"
                logger.exception("child_source_failed", extra={"source": repr(source)})
            finally:
                _capture_source_stats(source, result)
                if result.failed and not failed_before:
                    self.failed_sources += 1
                reset_source_deadline(deadline_token)
                reset_contextvars(**context_tokens)

    async def _fetch_parallel(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        _SENTINEL = object()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_capacity)
        source_queue: asyncio.Queue[Source[RawItem]] = asyncio.Queue()

        for source in self._sources:
            source_queue.put_nowait(source)

        async def _drain_one(source: Source[RawItem]) -> None:
            from job_ftch.infrastructure.sources.source_deadline import (
                reset_source_deadline,
                set_source_deadline,
            )

            source_id, source_kind, source_name = _source_identity(source)
            context_tokens = bind_contextvars(
                source_id=source_id,
                source_kind=source_kind,
            )
            result = self.source_results.setdefault(
                source_id,
                SourceFetchResult(
                    source_id=source_id,
                    source_kind=source_kind,
                    source_name=source_name,
                ),
            )
            failed_before = result.failed
            deadline_token = set_source_deadline(
                asyncio.get_running_loop().time() + self._hard_deadline_seconds
            )
            try:
                async with asyncio.timeout(self._hard_deadline_seconds):
                    async for item in source.fetch():
                        result.yielded += 1
                        await queue.put(item)
            except TimeoutError:
                result.evicted = True
                result.eviction_kind = "hard_deadline"
                result.deadline_exceeded = True
                result.hard_deadline_hit = True
                result.partial = result.yielded > 0
                result.failed = True
                result.error = "source_hard_deadline_exceeded"
            except Exception as exc:
                result.failed = True
                err_msg = str(exc).splitlines()[0] if str(exc) else "Unknown"
                result.error = f"{exc.__class__.__name__}: {err_msg}"
                logger.exception("child_source_failed", extra={"source": repr(source)})
            finally:
                _capture_source_stats(source, result)
                if result.failed and not failed_before:
                    self.failed_sources += 1
                reset_source_deadline(deadline_token)
                reset_contextvars(**context_tokens)

        async def _worker() -> None:
            while True:
                try:
                    source = source_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await _drain_one(source)

        async def _run_all() -> None:
            async with asyncio.TaskGroup() as tg:
                for _ in range(min(self._concurrency, len(self._sources))):
                    tg.create_task(_worker())
            await queue.put(_SENTINEL)

        producer = asyncio.create_task(_run_all())
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

    async def _fetch_parallel_dynamic(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        sentinel = object()
        result_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_capacity)
        overflow_queue: asyncio.Queue[_SourceStreamState | object] = asyncio.Queue()
        fast_limiter = anyio.CapacityLimiter(total_tokens=self._concurrency)
        overflow_tasks: list[asyncio.Task[None]] = []
        detached_producers: set[asyncio.Task[None]] = set()
        overflow_started = False
        overflow_lock = asyncio.Lock()

        def _observe_detached_producer(task: asyncio.Task[None]) -> None:
            detached_producers.add(task)

            def _consume_result(completed: asyncio.Task[None]) -> None:
                detached_producers.discard(completed)
                if not completed.cancelled():
                    with contextlib.suppress(Exception):
                        completed.exception()

            task.add_done_callback(_consume_result)

        async def _cancel_producer(state: _SourceStreamState) -> None:
            """Stop a source before publishing its terminal snapshot.

            A source normally exits within one bounded grace window.  A second
            bounded cancellation handles generators that consume the first
            signal while unwinding nested workers.  Python cannot force-kill a
            non-cooperative browser task in-process; such a task is detached
            only after both windows expire, and is still cancelled again during
            composite shutdown.  It can no longer mutate the frozen result.
            """
            if state.producer.done():
                return
            for _ in range(2):
                state.producer.cancel()
                done, _pending = await asyncio.wait(
                    {state.producer}, timeout=self._hard_cancel_grace_seconds
                )
                if state.producer in done:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        state.producer.result()
                    return
            _observe_detached_producer(state.producer)

        async def _forward_state(
            state: _SourceStreamState,
            *,
            deadline_seconds: float,
            hard_deadline: bool,
        ) -> bool:
            try:
                async with asyncio.timeout(deadline_seconds):
                    while True:
                        item = await state.queue.get()
                        if item is _QUEUE_DONE:
                            failed_before = state.result.failed
                            _capture_source_stats(state.source, state.result)
                            if state.result.failed and not failed_before:
                                self.failed_sources += 1
                            return True
                        if isinstance(item, tuple) and item and item[0] is _QUEUE_ERROR:
                            exc = item[1]
                            failed_before = state.result.failed
                            state.result.failed = True
                            err_msg = str(exc).splitlines()[0] if str(exc) else "Unknown"
                            state.result.error = f"{exc.__class__.__name__}: {err_msg}"
                            _capture_source_stats(state.source, state.result)
                            if state.result.failed and not failed_before:
                                self.failed_sources += 1
                            logger.exception(
                                "child_source_failed",
                                extra={"source": repr(state.source)},
                            )
                            return True
                        state.result.yielded += 1
                        await result_queue.put(item)
            except TimeoutError:
                if hard_deadline:
                    failed_before = state.result.failed
                    state.result.evicted = True
                    state.result.eviction_kind = "hard_deadline"
                    state.result.deadline_exceeded = True
                    state.result.hard_deadline_hit = True
                    state.result.failed = True
                    state.result.error = state.result.error or "source_hard_deadline_exceeded"
                    await _cancel_producer(state)
                    _capture_source_stats(state.source, state.result)
                    if state.result.failed and not failed_before:
                        self.failed_sources += 1
                    return True
                return False

        def _maybe_resize_tokens(*, overflowed: bool) -> None:
            if not self._adaptive_resize:
                return
            current = int(fast_limiter.total_tokens)
            if overflowed:
                fast_limiter.total_tokens = max(1, current - 1)
                return
            if current < self._concurrency_max:
                fast_limiter.total_tokens = current + 1

        async def _start_overflow_workers() -> None:
            nonlocal overflow_started
            async with overflow_lock:
                if overflow_started:
                    return
                overflow_started = True
                self.overflow_workers_started = self._overflow_concurrency
                for _ in range(self._overflow_concurrency):
                    overflow_tasks.append(asyncio.create_task(_overflow_worker()))

        async def _produce_items(
            source: Source[RawItem],
            queue: asyncio.Queue[object],
            deadline_at: float,
        ) -> None:
            from job_ftch.infrastructure.sources.source_deadline import (
                reset_source_deadline,
                set_source_deadline,
            )

            source_id, source_kind, _source_name = _source_identity(source)
            context_tokens = bind_contextvars(
                source_id=source_id,
                source_kind=source_kind,
            )
            deadline_token = set_source_deadline(deadline_at)
            cancelled = False
            try:
                async for item in source.fetch():
                    await queue.put(item)
            except asyncio.CancelledError:
                # ``queue.put`` in the normal completion path is deliberately
                # backpressured.  Once the consumer has been evicted, however,
                # waiting for room to publish a terminal marker prevents the
                # cancelled producer (and any browser subprocess it owns) from
                # unwinding before the event loop closes.
                cancelled = True
                raise
            except Exception as exc:
                await queue.put((_QUEUE_ERROR, exc))
            finally:
                if cancelled:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(_QUEUE_DONE)
                else:
                    await queue.put(_QUEUE_DONE)
                reset_source_deadline(deadline_token)
                reset_contextvars(**context_tokens)

        async def _run_source(source: Source[RawItem]) -> None:
            async with fast_limiter:
                source_id, source_kind, source_name = _source_identity(source)
                result = self.source_results.setdefault(
                    source_id,
                    SourceFetchResult(
                        source_id=source_id,
                        source_kind=source_kind,
                        source_name=source_name,
                    ),
                )
                queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_capacity)
                started_at = asyncio.get_running_loop().time()
                deadline_at = started_at + self._hard_deadline_seconds
                state = _SourceStreamState(
                    source=source,
                    result=result,
                    queue=queue,
                    producer=asyncio.create_task(_produce_items(source, queue, deadline_at)),
                    started_at=started_at,
                    deadline_at=deadline_at,
                )
                remaining = state.deadline_at - asyncio.get_running_loop().time()
                completed = await _forward_state(
                    state,
                    deadline_seconds=min(self._soft_deadline_seconds, max(0.0, remaining)),
                    hard_deadline=False,
                )
                if completed:
                    _maybe_resize_tokens(overflowed=False)
                    return
                state.result.soft_deadline_hit = True
                _maybe_resize_tokens(overflowed=True)
                await _start_overflow_workers()
                await overflow_queue.put(state)

        async def _overflow_worker() -> None:
            while True:
                state = await overflow_queue.get()
                if state is sentinel:
                    return
                assert isinstance(state, _SourceStreamState)
                remaining = state.deadline_at - asyncio.get_running_loop().time()
                completed = await _forward_state(
                    state,
                    # Queue waiting consumes the same source budget.  An
                    # overflow worker must never grant a second full slice.
                    deadline_seconds=max(0.0, remaining),
                    hard_deadline=True,
                )
                if completed and state.result.eviction_kind is None and state.result.failed:
                    state.result.evicted = True
                    state.result.eviction_kind = "soft_deadline"
                if not state.result.evicted:
                    with contextlib.suppress(asyncio.CancelledError):
                        await state.producer

        producer = asyncio.create_task(
            self._run_dynamic_sources(
                result_queue=result_queue,
                overflow_queue=overflow_queue,
                sentinel=sentinel,
                overflow_started_getter=lambda: overflow_started,
                overflow_tasks=overflow_tasks,
                run_source=_run_source,
            )
        )
        try:
            while True:
                item = await result_queue.get()
                if item is sentinel:
                    break
                yield item  # type: ignore[misc]
        finally:
            pending_detached = tuple(detached_producers)
            for task in pending_detached:
                if not task.done():
                    task.cancel()
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
            # Do not let a detached browser producer survive until
            # ``asyncio.run`` tears down the Proactor loop.  On Windows that
            # leaves Patchright's child-process pipes unclosed and produces
            # ``unclosed transport`` ResourceWarnings.  Teardown is allowed
            # one bounded grace window: sources are already frozen and cannot
            # emit more items.
            if pending_detached:
                done, still_pending = await asyncio.wait(
                    pending_detached, timeout=self._hard_cancel_grace_seconds
                )
                for task in done:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        task.result()
                if still_pending:
                    for task in still_pending:
                        task.cancel()
                    with contextlib.suppress(Exception):
                        await asyncio.gather(*still_pending, return_exceptions=True)
                    logger.error(
                        "detached_source_producers_failed_to_close",
                        extra={"remaining": len(still_pending)},
                    )

    async def _run_dynamic_sources(
        self,
        *,
        result_queue: asyncio.Queue[object],
        overflow_queue: asyncio.Queue[_SourceStreamState | object],
        sentinel: object,
        overflow_started_getter: Any,
        overflow_tasks: list[asyncio.Task[None]],
        run_source: Any,
    ) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                for source in self._sources:
                    tg.create_task(run_source(source))
            if overflow_started_getter():
                for _ in range(self._overflow_concurrency):
                    await overflow_queue.put(sentinel)
                await asyncio.gather(*overflow_tasks)
        finally:
            for task in overflow_tasks:
                if not task.done():
                    task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*overflow_tasks, return_exceptions=True)
            await result_queue.put(sentinel)
