"""Distributed session simulation with Poisson traffic patterns.

Simulates realistic traffic distribution via Poisson process:
- Generate arrival times: Exponential(λ) distribution
- Cumulative sum = arrival times
- Execute tasks with Poisson timing

Real traffic follows Poisson distribution, not burst patterns.
1000 requests over an hour ≠ 1000 requests in 2 seconds.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger("job_ftch.bypass.distributed_simulator")


class DistributedSimulator:
    """Simulate realistic traffic distribution via Poisson process.

    Usage:
        simulator = DistributedSimulator(target_rpm=10, seed=42)
        await simulator.execute_with_poisson_timing(tasks)
    """

    def __init__(self, target_rpm: float = 10.0, seed: int | None = None) -> None:
        """Initialize simulator.

        Args:
            target_rpm: Target requests per minute (Poisson rate λ)
            seed: Random seed for deterministic testing
        """
        self._target_rpm = target_rpm
        self._rng = random.Random(seed)

    def generate_arrival_times(self, count: int) -> list[float]:
        """Generate arrival times for Poisson process.

        Inter-arrival times follow Exponential(λ) distribution.
        Cumulative sum gives absolute arrival times.

        Args:
            count: Number of arrival times to generate

        Returns:
            List of arrival times in seconds from start
        """
        lambda_rate = self._target_rpm / 60.0  # Convert to per-second
        inter_arrivals = [self._rng.expovariate(lambda_rate) for _ in range(count)]

        # Cumulative sum = absolute arrival times
        arrival_times = []
        current_time = 0.0
        for inter in inter_arrivals:
            current_time += inter
            arrival_times.append(current_time)

        return arrival_times

    def generate_burst_arrivals(
        self,
        count: int,
        burst_start: float,
        burst_duration: float,
    ) -> list[float]:
        """Generate arrivals concentrated in a time window.

        Useful for simulating peak hours or specific time windows.

        Args:
            count: Number of arrivals
            burst_start: Start time of burst (seconds)
            burst_duration: Duration of burst (seconds)

        Returns:
            List of arrival times within the burst window
        """
        return [burst_start + self._rng.uniform(0, burst_duration) for _ in range(count)]

    async def execute_with_poisson_timing(
        self,
        tasks: list[Callable[[], Awaitable[Any]]],
        *,
        max_concurrent: int = 5,
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> list[Any]:
        """Execute tasks with Poisson timing.

        Tasks are executed at times determined by Poisson process.
        Respects max_concurrent limit for parallel execution.

        Args:
            tasks: List of async callables to execute
            max_concurrent: Maximum concurrent tasks
            progress_callback: Optional callback(completed, total)

        Returns:
            List of task results
        """
        if not tasks:
            return []

        arrival_times = self.generate_arrival_times(len(tasks))
        results: list[Any] = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def execute_task(index: int, task: Callable[[], Awaitable[Any]]) -> Any:
            async with semaphore:
                try:
                    result = await task()
                    results.append(result)
                    if progress_callback:
                        await progress_callback(len(results), len(tasks))
                    return result
                except Exception as exc:
                    logger.warning(
                        "task_execution_failed",
                        index=index,
                        error=str(exc),
                    )
                    results.append(None)
                    return None

        start_time = time.time()

        # Schedule tasks at their arrival times
        scheduled_tasks = []
        for i, (task, arrival_time) in enumerate(zip(tasks, arrival_times, strict=True)):
            # Wait until arrival time
            elapsed = time.time() - start_time
            wait_time = max(0, arrival_time - elapsed)
            await asyncio.sleep(wait_time)

            # Execute task
            scheduled_tasks.append(asyncio.create_task(execute_task(i, task)))

        # Wait for all tasks to complete
        await asyncio.gather(*scheduled_tasks)

        return results

    async def execute_with_uniform_timing(
        self,
        tasks: list[Callable[[], Awaitable[Any]]],
        *,
        interval: float = 1.0,
        max_concurrent: int = 5,
    ) -> list[Any]:
        """Execute tasks with uniform timing (baseline for comparison).

        Tasks are executed at fixed intervals.

        Args:
            tasks: List of async callables to execute
            interval: Fixed interval between tasks (seconds)
            max_concurrent: Maximum concurrent tasks

        Returns:
            List of task results
        """
        if not tasks:
            return []

        results: list[Any] = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def execute_task(task: Callable[[], Awaitable[Any]]) -> Any:
            async with semaphore:
                try:
                    result = await task()
                    results.append(result)
                    return result
                except Exception as exc:
                    logger.warning("task_execution_failed", error=str(exc))
                    results.append(None)
                    return None

        for task in tasks:
            await asyncio.create_task(execute_task(task))
            await asyncio.sleep(interval)

        return results

    def estimate_completion_time(self, task_count: int) -> float:
        """Estimate total completion time for given number of tasks.

        Args:
            task_count: Number of tasks to execute

        Returns:
            Estimated completion time in seconds
        """
        arrival_times = self.generate_arrival_times(task_count)
        return arrival_times[-1] if arrival_times else 0.0
