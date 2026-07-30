"""Human-like trajectory helpers used by behavior simulation."""

from __future__ import annotations

import pytest

from job_ftch.infrastructure.bypass.behavior_sim import (
    _BezierCalculator,
    _ease_in_out_cubic,
    _ease_in_out_quad,
    _ease_in_out_quint,
    _ease_in_out_sine,
    _ease_out_quad,
    _generate_trajectory,
)


def test_trajectory_has_origin_and_near_target() -> None:
    trajectory = _generate_trajectory((0, 0), (100, 100))
    assert trajectory[0] == (0, 0)
    assert len(trajectory) > 0
    assert abs(trajectory[-1][0] - 100) < 5
    assert abs(trajectory[-1][1] - 100) < 5


def test_trajectory_honors_custom_point_count() -> None:
    assert len(_generate_trajectory((10, 20), (500, 400), target_points=50, knots_count=4)) == 50


def test_bezier_calculator_preserves_endpoints() -> None:
    points = [(0.0, 0.0), (50.0, 100.0), (100.0, 0.0)]
    trajectory = _BezierCalculator.calculate_points(10, points)
    assert len(trajectory) == 10
    assert trajectory[0] == (0, 0)
    assert trajectory[-1] == (100, 0)


@pytest.mark.parametrize(
    "ease",
    [_ease_out_quad, _ease_in_out_quad, _ease_in_out_sine, _ease_in_out_cubic, _ease_in_out_quint],
)
def test_easing_functions_stay_in_unit_interval(ease) -> None:
    assert all(0.0 <= ease(value) <= 1.0 for value in (0.0, 0.25, 0.5, 0.75, 1.0))
