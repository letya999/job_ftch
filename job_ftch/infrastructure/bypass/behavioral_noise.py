"""Behavioral noise injection for unique action patterns.

Adds microscopic random deviations to every browser action:
- Mouse jitter: ±0.5px per coordinate
- Typing variance: ±20ms per keystroke
- Scroll overshoot: ±5px overshoot
- Click pressure: 50-150ms duration

Each action becomes unique, preventing cross-bot correlation.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any


class BehavioralNoise:
    """Add microscopic noise to browser actions.

    Usage:
        noise = BehavioralNoise(seed=42)
        jittered = noise.add_mouse_jitter([(100, 200), (150, 250)])
        typing_delay = noise.typing_delay()
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def add_mouse_jitter(self, trajectory: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Add microscopic jitter to mouse trajectory.

        Each coordinate gets ±0.5px noise (Normal distribution).
        """
        jittered = []
        for x, y in trajectory:
            jitter_x = self._rng.gauss(0, 0.5)
            jitter_y = self._rng.gauss(0, 0.5)
            jittered.append((int(x + jitter_x), int(y + jitter_y)))
        return jittered

    def add_scroll_overshoot(self, target_y: int) -> int:
        """Add overshoot to scroll target.

        Real users often overshoot and correct. ±5px overshoot.
        """
        overshoot = self._rng.gauss(0, 5)
        return int(target_y + overshoot)

    def typing_delay(self) -> float:
        """Generate unique delay between keystrokes.

        Base delay 120ms ± 20ms (Normal distribution).
        """
        delay = self._rng.gauss(0.12, 0.02)
        return max(0.03, delay)

    def click_duration(self) -> float:
        """Generate realistic click duration.

        Real clicks last 50-150ms (Uniform distribution).
        """
        return self._rng.uniform(0.05, 0.15)

    def scroll_amount(self, viewport_height: int) -> int:
        """Generate realistic scroll amount.

        Most scrolls are 200-600px, some are full viewport.
        """
        if self._rng.random() < 0.2:
            # 20% chance of full viewport scroll
            return viewport_height
        # 80% chance of partial scroll
        return self._rng.randint(200, 600)

    async def apply_to_mouse_move(self, page: Any, x: int, y: int) -> None:
        """Move mouse with noise applied.

        Adds jitter to final position before moving.
        """
        jittered_x = x + int(self._rng.gauss(0, 0.5))
        jittered_y = y + int(self._rng.gauss(0, 0.5))
        await page.mouse.move(jittered_x, jittered_y)

    async def apply_to_click(self, page: Any, x: int, y: int) -> None:
        """Click with realistic duration and noise.

        Adds jitter to position and realistic click duration.
        """
        jittered_x = x + int(self._rng.gauss(0, 0.5))
        jittered_y = y + int(self._rng.gauss(0, 0.5))

        # Mouse down
        await page.mouse.move(jittered_x, jittered_y)
        await page.mouse.down()

        # Hold for realistic duration
        await asyncio.sleep(self.click_duration())

        # Mouse up
        await page.mouse.up()

    async def apply_to_type(self, page: Any, text: str) -> None:
        """Type text with unique delays per keystroke.

        Each character has unique timing based on Gaussian distribution.
        """
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(self.typing_delay())

            # Occasional longer pause (thinking)
            if self._rng.random() < 0.05:
                await asyncio.sleep(self._rng.uniform(0.3, 0.8))

    async def apply_to_scroll(self, page: Any, direction: str = "down") -> None:
        """Scroll with realistic amount and overshoot.

        Adds overshoot and correction, mimicking real user behavior.
        """
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        viewport_height = viewport["height"]

        # Generate scroll amount
        amount = self.scroll_amount(viewport_height)

        # Add overshoot
        if direction == "down":
            overshoot_amount = amount + int(self._rng.gauss(0, 20))
            await page.evaluate(f"window.scrollBy(0, {overshoot_amount})")

            # Correction (scroll back slightly)
            if self._rng.random() < 0.3:
                correction = self._rng.randint(5, 20)
                await asyncio.sleep(self._rng.uniform(0.1, 0.3))
                await page.evaluate(f"window.scrollBy(0, -{correction})")
        else:  # up
            overshoot_amount = amount + int(self._rng.gauss(0, 20))
            await page.evaluate(f"window.scrollBy(0, -{overshoot_amount})")

            # Correction
            if self._rng.random() < 0.3:
                correction = self._rng.randint(5, 20)
                await asyncio.sleep(self._rng.uniform(0.1, 0.3))
                await page.evaluate(f"window.scrollBy(0, {correction})")
