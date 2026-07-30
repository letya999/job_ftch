"""Temporal request shaping with realistic statistical distributions.

Replaces uniform random delays with realistic distributions that mimic
human behavior:
- Reading time: LogNormal (median ~30s, some 5 min)
- Thinking time: Pareto (heavy-tailed)
- Scroll pause: Gamma
- Inter-arrival: Exponential (Poisson process)

Anti-bot systems detect uniform distribution as bot-like. Statistical
distributions match real human behavior patterns.
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import Any


class TemporalShaper:
    """Generate realistic timing delays using statistical distributions.

    Usage:
        shaper = TemporalShaper(seed=42)
        reading_time = shaper.reading_time()  # LogNormal
        thinking_time = shaper.thinking_time()  # Pareto
        scroll_pause = shaper.scroll_pause()  # Gamma
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def reading_time(self) -> float:
        """Generate realistic reading time (LogNormal distribution).

        Median ~30 seconds, some users read for 5 minutes.
        LogNormal(μ=3.5, σ=0.8) → median ≈ 33s, mean ≈ 40s
        """
        mu = 3.5
        sigma = 0.8
        z = self._rng.gauss(mu, sigma)
        return math.exp(z)

    def thinking_time(self) -> float:
        """Generate realistic thinking time (Pareto distribution).

        Heavy-tailed: most users are quick, but some think for a long time.
        Pareto(scale=5, shape=2) → median ≈ 7s, mean ≈ 10s
        """
        scale = 5.0
        shape = 2.0
        u = self._rng.random()
        return float(scale / ((1 - u) ** (1 / shape)))

    def scroll_pause(self) -> float:
        """Generate realistic pause between scrolls (Gamma distribution).

        Gamma(shape=2, scale=1.5) → mean ≈ 3s, most pauses 1-5s
        """
        shape = 2.0
        scale = 1.5
        return self._rng.gammavariate(shape, scale)

    def inter_arrival(self, rate_per_minute: float = 10.0) -> float:
        """Generate inter-arrival time for Poisson process.

        Exponential(λ) where λ = rate_per_minute / 60
        """
        lambda_rate = rate_per_minute / 60.0
        return self._rng.expovariate(lambda_rate)

    async def simulate_reading(self, page: Any) -> None:
        """Simulate realistic page reading behavior.

        Scrolls page with realistic pauses, mimicking human reading pattern.
        """
        # Initial reading pause
        await asyncio.sleep(self.reading_time())

        # Scroll through page with pauses
        num_scrolls = self._rng.randint(3, 10)
        for _ in range(num_scrolls):
            # Scroll down
            scroll_amount = self._rng.randint(200, 600)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")

            # Pause (thinking time)
            await asyncio.sleep(self.scroll_pause())

        # Final reading pause at bottom
        await asyncio.sleep(self.thinking_time() * 0.5)

    async def simulate_typing(self, page: Any, selector: str, text: str) -> None:
        """Simulate realistic typing with variable delays.

        Each keystroke has unique timing based on Gaussian distribution.
        """
        element = await page.query_selector(selector)
        if not element:
            return

        await element.click()
        await asyncio.sleep(self._rng.uniform(0.1, 0.3))

        for char in text:
            await page.keyboard.type(char)
            # Variable delay between keystrokes
            delay = self._rng.gauss(0.12, 0.03)
            await asyncio.sleep(max(0.03, delay))

            # Occasional longer pause (thinking)
            if self._rng.random() < 0.05:
                await asyncio.sleep(self._rng.uniform(0.3, 0.8))
