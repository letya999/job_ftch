"""Human-like behavioral simulation bypass tier.

Ported and adapted from Botasaurus humancursor (omkarcloud/botasaurus):
- Bezier trajectory math (human_curve_generator.py)
- Random curve parameter generation (calculate_and_randomize.py)
- Stdlib-only: no numpy/pytweening dependency

Provides natural mouse movement, scrolling, and click timing
to defeat simple bot-detection heuristics.
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

from job_ftch.application.registry import register_bypass

logger = structlog.get_logger("job_ftch.bypass.behavior_sim")

# ---------------------------------------------------------------------------
# Easing functions (stdlib-only replacement for pytweening)
# ---------------------------------------------------------------------------


def _ease_out_quad(t: float) -> float:
    return t * (2 - t)


def _ease_in_out_quad(t: float) -> float:
    if t < 0.5:
        return 2 * t * t
    return -1 + (4 - 2 * t) * t


def _ease_in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1) / 2


def _ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    return 1 - (-2 * t + 2) ** 3 / 2


def _ease_in_out_quint(t: float) -> float:
    if t < 0.5:
        return 16 * t * t * t * t * t
    return 1 - (-2 * t + 2) ** 5 / 2


_EASING_FUNCS: list[Callable[[float], float]] = [
    _ease_out_quad,
    _ease_in_out_quad,
    _ease_in_out_sine,
    _ease_in_out_cubic,
    _ease_in_out_quint,
]

# ---------------------------------------------------------------------------
# Bezier curve calculator (pure math, ported from Botasaurus)
# ---------------------------------------------------------------------------


class _BezierCalculator:
    """Bernstein-polynomial Bezier curve evaluator."""

    @staticmethod
    def _binomial(n: int, k: int) -> float:
        return math.factorial(n) / float(math.factorial(k) * math.factorial(n - k))

    @staticmethod
    def _bernstein_poly_point(x: float, i: int, n: int) -> float:
        return _BezierCalculator._binomial(n, i) * (x**i) * ((1 - x) ** (n - i))

    @staticmethod
    def _make_bernstein(
        points: list[tuple[float, float]],
    ) -> Callable[[float], tuple[float, float]]:
        def bernstein(t: float) -> tuple[float, float]:
            n = len(points) - 1
            x = y = 0.0
            for i, (px, py) in enumerate(points):
                b = _BezierCalculator._bernstein_poly_point(t, i, n)
                x += px * b
                y += py * b
            return (x, y)

        return bernstein

    @staticmethod
    def calculate_points(n: int, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Return *n* points along the Bezier curve defined by *points*."""
        fn = _BezierCalculator._make_bernstein(points)
        return [fn(i / (n - 1)) for i in range(n)]


# ---------------------------------------------------------------------------
# Humanized trajectory generator (ported from Botasaurus humancursor)
# ---------------------------------------------------------------------------


def _generate_trajectory(
    from_point: tuple[int, int],
    to_point: tuple[int, int],
    *,
    offset_boundary_x: int = 80,
    offset_boundary_y: int = 80,
    knots_count: int = 2,
    distortion_mean: float = 1.0,
    distortion_st_dev: float = 1.0,
    distortion_frequency: float = 0.5,
    easing: Callable[[float], float] = _ease_out_quad,
    target_points: int = 100,
) -> list[tuple[float, float]]:
    """Generate a human-like Bezier trajectory between two points."""
    min_x = min(from_point[0], to_point[0])
    max_x = max(from_point[0], to_point[0])
    min_y = min(from_point[1], to_point[1])
    max_y = max(from_point[1], to_point[1])

    left = min_x - offset_boundary_x
    right = max_x + offset_boundary_x
    down = min_y - offset_boundary_y
    up = max_y + offset_boundary_y

    # Generate random internal knots within boundaries
    knots: list[tuple[float, float]] = []
    for _ in range(knots_count):
        kx = random.uniform(left, right)
        ky = random.uniform(down, up)
        knots.append((kx, ky))

    # Calculate Bezier points (count proportional to distance)
    dist = math.hypot(to_point[0] - from_point[0], to_point[1] - from_point[1])
    point_count = max(int(dist), 2)
    raw_points = _BezierCalculator.calculate_points(point_count, [from_point] + knots + [to_point])

    # Apply Gaussian distortion to interior points
    distorted = [raw_points[0]]
    for x, y in raw_points[1:-1]:
        delta = (
            random.gauss(distortion_mean, distortion_st_dev)
            if random.random() < distortion_frequency
            else 0.0
        )
        distorted.append((x, y + delta))
    distorted.append(raw_points[-1])

    # Resample to target_points using easing
    resampled: list[tuple[float, float]] = []
    for i in range(target_points):
        t = easing(i / (target_points - 1))
        idx = int(t * (len(distorted) - 1))
        idx = min(idx, len(distorted) - 1)
        resampled.append(distorted[idx])

    return resampled


def _random_curve_params(
    from_point: tuple[int, int],
    to_point: tuple[int, int],
    viewport_w: int,
    viewport_h: int,
) -> dict[str, Any]:
    """Generate randomized curve parameters (mirrors Botasaurus calculate_and_randomize)."""
    # Offset boundary — weighted random ranges
    offset_boundary_x = random.choice(
        random.choices(
            [range(20, 45), range(45, 75), range(75, 100)],
            weights=[0.2, 0.65, 0.15],
        )[0]
    )
    offset_boundary_y = random.choice(
        random.choices(
            [range(20, 45), range(45, 75), range(75, 100)],
            weights=[0.2, 0.65, 0.15],
        )[0]
    )

    # Knots count — weighted distribution
    knots_count = random.choices(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        weights=[0.15, 0.36, 0.17, 0.12, 0.08, 0.04, 0.03, 0.02, 0.015, 0.005],
    )[0]

    # Distortion — random within sensible ranges
    distortion_mean = random.uniform(0.80, 1.10)
    distortion_st_dev = random.uniform(0.85, 1.10)
    distortion_frequency = random.uniform(0.25, 0.70)

    # Target points based on distance
    dist = math.hypot(to_point[0] - from_point[0], to_point[1] - from_point[1])
    target_points = _calculate_step_count(dist)

    # Simplify if near viewport edges
    min_w, max_w = viewport_w * 0.15, viewport_w * 0.85
    min_h, max_h = viewport_h * 0.15, viewport_h * 0.85
    for pt in (from_point, to_point):
        if not (min_w <= pt[0] <= max_w and min_h <= pt[1] <= max_h):
            offset_boundary_x = 1
            offset_boundary_y = 1
            knots_count = 1

    easing = random.choice(_EASING_FUNCS)

    return {
        "offset_boundary_x": offset_boundary_x,
        "offset_boundary_y": offset_boundary_y,
        "knots_count": knots_count,
        "distortion_mean": distortion_mean,
        "distortion_st_dev": distortion_st_dev,
        "distortion_frequency": distortion_frequency,
        "easing": easing,
        "target_points": target_points,
    }


def _calculate_step_count(distance: float) -> int:
    """Scale step count with pixel distance (mirrors Botasaurus)."""
    if distance <= 500:
        lo, hi, base = 40, 50, 500
    elif distance <= 1000:
        lo, hi, base = 50, 60, 1000
    elif distance <= 1500:
        lo, hi, base = 60, 70, 1500
    elif distance <= 2000:
        lo, hi, base = 70, 80, 2000
    else:
        lo, hi, base = 80, 100, 2202
    raw = lo + (distance / base) * (hi - lo)
    raw = int(raw * random.uniform(0.9, 1.1))
    return max(lo, min(hi, raw))


# ---------------------------------------------------------------------------
# Bypass implementation
# ---------------------------------------------------------------------------


class BehaviorSimBypass:
    """Composable decorator that adds human-like mouse trajectories and timing.

    This is NOT a standalone escalation tier. It does not own a browser
    runtime and provides no anti-detect fingerprint protection on its own.
    It is composed by ``AdaptiveBypassManager`` on top of whatever browser
    tier is currently active (stealth_browser, camoufox, nodriver, …).

    For HTTP-only tiers (noop, curl_stealth) ``apply_page`` is never
    called by ``browser_utils``, so the decorator stays dormant.

    It is still registered as ``@register_bypass("behavior_sim")`` for
    manual use and unit testing, but it is deliberately absent from
    ``DEFAULT_TIER_ORDER`` in the adaptive escalation chain.
    """

    def __init__(
        self,
        min_delay: float = 0.5,
        max_delay: float = 2.0,
        trajectory: bool = True,
    ) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._trajectory = trajectory
        self._noise_typing_delay: float | None = None
        self._noise_click_duration: float | None = None

        from job_ftch.infrastructure.bypass.cognitive_state import CognitiveStateMachine

        self._cognitive = CognitiveStateMachine()

    def update_noise_params(self, metadata: dict[str, Any]) -> None:
        """Consume orchestrator metadata to calibrate BehaviorSim timing."""
        behavioral = metadata.get("behavioral")
        if behavioral:
            self._noise_typing_delay = behavioral.get("typing_delay")
            self._noise_click_duration = behavioral.get("click_duration")

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        """Simulate human-like scrolling and mouse movement on the page."""
        try:
            from job_ftch.infrastructure.bypass.cognitive_state import UserEvent

            behavior_params = self._cognitive.get_behavior_params()

            delay = random.uniform(self._min_delay, self._max_delay)
            delay *= behavior_params.session_duration_factor
            await asyncio.sleep(delay)

            scroll_pause = max(
                0.1,
                random.gauss(
                    behavior_params.scroll_pause_mean * 0.05,
                    behavior_params.scroll_pause_std * 0.05,
                ),
            )
            await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
            await asyncio.sleep(scroll_pause)
            await page.evaluate("window.scrollBy(0, window.innerHeight / 3)")
            await asyncio.sleep(scroll_pause * random.uniform(0.8, 1.2))

            self._cognitive.transition(UserEvent(event_type="scroll", magnitude=300))

            if not hasattr(page, "mouse"):
                return

            viewport = page.viewport_size or {"width": 1280, "height": 720}
            vw, vh = viewport["width"], viewport["height"]

            start = (random.randint(50, vw - 50), random.randint(50, vh - 50))
            end = (random.randint(50, vw - 50), random.randint(50, vh - 50))

            if self._trajectory:
                await self._move_with_trajectory(page, start, end, vw, vh)
            else:
                await page.mouse.move(end[0], end[1], steps=10)

        except Exception:
            logger.debug("behavior_sim_page_error", exc_info=True)

    async def move_to(
        self,
        page: Any,
        target_x: int,
        target_y: int,
    ) -> None:
        """Move mouse to (target_x, target_y) with a humanized Bezier trajectory."""
        if not hasattr(page, "mouse"):
            return
        try:
            viewport = page.viewport_size or {"width": 1280, "height": 720}
            vw, vh = viewport["width"], viewport["height"]

            start = (vw // 2, vh // 2)
            await self._move_with_trajectory(page, start, (target_x, target_y), vw, vh)
        except Exception:
            logger.debug("behavior_sim_move_error", exc_info=True)

    async def click_with_trajectory(
        self,
        page: Any,
        target_x: int,
        target_y: int,
    ) -> None:
        """Move to element with Bezier trajectory, then click."""
        if not hasattr(page, "mouse"):
            return
        await self.move_to(page, target_x, target_y)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.mouse.click(target_x, target_y)

    async def type_with_trajectory(
        self,
        page: Any,
        selector: str,
        text: str,
        *,
        clear_first: bool = True,
    ) -> None:
        """Focus element with Bezier trajectory, then type with human-like delays.

        Inter-key delays follow a Gaussian distribution to mimic real typing.
        """
        try:
            if not hasattr(page, "mouse") or not hasattr(page, "keyboard"):
                return
            element = await page.query_selector(selector)
            if not element:
                return
            box = await element.bounding_box()
            if not box:
                return
            target_x = int(box["x"] + box["width"] / 2)
            target_y = int(box["y"] + box["height"] / 2)
            await self.move_to(page, target_x, target_y)
            await asyncio.sleep(random.uniform(0.08, 0.2))
            await page.mouse.click(target_x, target_y)
            await asyncio.sleep(random.uniform(0.1, 0.25))
            if clear_first:
                await page.keyboard.press("Control+a")
                await asyncio.sleep(random.uniform(0.05, 0.12))
                await page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.1, 0.2))
            base_typing_delay = self._noise_typing_delay or 0.12
            for char in text:
                await page.keyboard.type(char)
                delay = max(0.03, random.gauss(base_typing_delay, 0.04))
                await asyncio.sleep(delay)
                if random.random() < 0.05:
                    await asyncio.sleep(random.uniform(0.2, 0.5))
        except Exception:
            logger.debug("type_with_trajectory_error", exc_info=True)

    async def _move_with_trajectory(
        self,
        page: Any,
        start: tuple[int, int],
        end: tuple[int, int],
        viewport_w: int,
        viewport_h: int,
    ) -> None:
        """Generate and execute a Bezier trajectory with overshoot and micro-correction."""
        params = _random_curve_params(start, end, viewport_w, viewport_h)
        trajectory = _generate_trajectory(start, end, **params)

        for x, y in trajectory:
            await page.mouse.move(int(x), int(y))
            await asyncio.sleep(random.uniform(0.005, 0.02))

        if random.random() < 0.6:
            overshoot_x = int(end[0]) + random.randint(-4, 4)
            overshoot_y = int(end[1]) + random.randint(-3, 3)
            await page.mouse.move(overshoot_x, overshoot_y)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            correction_x = int(end[0]) + random.randint(-1, 1)
            correction_y = int(end[1]) + random.randint(-1, 1)
            await page.mouse.move(correction_x, correction_y)
            await asyncio.sleep(random.uniform(0.01, 0.03))
            await page.mouse.move(int(end[0]), int(end[1]))


@register_bypass("behavior_sim")
def _create_behavior_sim(
    bypass_config: dict[str, Any] | None = None,
) -> BehaviorSimBypass:
    config = bypass_config or {}
    return BehaviorSimBypass(
        min_delay=float(config.get("min_delay", "0.5")),
        max_delay=float(config.get("max_delay", "2.0")),
        trajectory=config.get("trajectory", "true").lower() != "false",
    )
