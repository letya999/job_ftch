import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable

import structlog

from application.pipeline import RunSummary
from application.source_loader import load_sources
from config import Settings

logger = structlog.get_logger(__name__)


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        run_fn: Callable[[Settings], Awaitable[RunSummary]],
    ) -> None:
        self.settings = settings
        self.run_fn = run_fn
        self._stop_event = asyncio.Event()
        self._run_index = 0
        self._interval: int | None = None

    async def run_forever(self) -> None:
        """Loop: run pipeline, sleep interval, repeat. Handles SIGINT/SIGTERM."""
        # Handle signals
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop_event.set)
        except NotImplementedError:
            # Signal handlers not supported on some Windows environments
            pass

        logger.info("scheduler_started", interval_seconds=self._calculate_interval())

        try:
            while not self._stop_event.is_set():
                self._run_index += 1
                logger.info("starting_scheduled_run", run_index=self._run_index)

                try:
                    summary = await self.run_fn(self.settings)
                    summary.scheduled_run_index = self._run_index
                    logger.info(
                        "scheduled_run_finished",
                        run_index=self._run_index,
                        emitted=summary.emitted,
                    )
                except Exception as e:
                    logger.exception(
                        "scheduled_run_failed", run_index=self._run_index, error=str(e)
                    )

                if self._stop_event.is_set():
                    break

                interval = self._calculate_interval()
                logger.debug("scheduler_sleeping", interval_seconds=interval)

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=float(interval))
        finally:
            logger.info("scheduler_shutting_down")
            # Clean up signal handlers
            try:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass

    def _calculate_interval(self) -> int:
        if self._interval is not None:
            return self._interval

        intervals = []
        if self.settings.sources_file_path and self.settings.sources_file_path.exists():
            try:
                specs = load_sources(self.settings.sources_file_path)
                for spec in specs:
                    if hasattr(spec, "interval_seconds") and spec.interval_seconds:
                        intervals.append(spec.interval_seconds)
            except Exception:
                logger.exception("failed_to_load_sources_for_interval_calculation")

        self._interval = (
            min(intervals) if intervals else (self.settings.schedule_interval_seconds or 3600)
        )
        return self._interval
