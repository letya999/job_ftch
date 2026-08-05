from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from itertools import pairwise

from paritylab.models import BehaviorEvent


@dataclass(frozen=True, slots=True)
class PointerKinematics:
    samples: int
    duration_ms: float
    distance_px: float
    displacement_px: float
    path_efficiency: float
    mean_speed_px_s: float
    peak_speed_px_s: float
    speed_cv: float | None
    acceleration_cv: float | None
    mean_abs_jerk_px_s3: float | None
    direction_changes: int
    curvature_mean: float | None
    pause_ratio: float


@dataclass(frozen=True, slots=True)
class CadenceFeatures:
    samples: int
    mean_ms: float | None
    cv: float | None
    repeated_interval_ratio: float


@dataclass(frozen=True, slots=True)
class KeyboardFeatures:
    cadence: CadenceFeatures
    paired_keys: int
    mean_dwell_ms: float | None
    dwell_cv: float | None
    zero_dwell_ratio: float | None
    correction_count: int
    repeat_count: int


@dataclass(frozen=True, slots=True)
class ScrollFeatures:
    cadence: CadenceFeatures
    fixed_delta_ratio: float | None
    direction_reversals: int
    decay_ratio: float | None
    frame_aligned_ratio: float | None


@dataclass(frozen=True, slots=True)
class ContactFeatures:
    samples: int
    pointer_ids: int
    pressure_cv: float | None
    geometry_cv: float | None
    zero_pressure_move_ratio: float | None
    tilt_samples: int
    tilt_cv: float | None
    continuity_breaks: int


@dataclass(frozen=True, slots=True)
class BehaviorFeatures:
    pointer: PointerKinematics | None
    keyboard: KeyboardFeatures
    scroll: ScrollFeatures
    touch: ContactFeatures
    pen: ContactFeatures
    click_target_misses: int


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / abs(mean) if mean else None


def _cadence(events: list[BehaviorEvent]) -> CadenceFeatures:
    intervals = [
        current.since_navigation_ms - previous.since_navigation_ms
        for previous, current in pairwise(events)
        if current.since_navigation_ms >= previous.since_navigation_ms
    ]
    rounded = [round(value, 1) for value in intervals]
    repeated = 0.0
    if rounded:
        repeated = 1 - (len(set(rounded)) / len(rounded))
    return CadenceFeatures(
        samples=len(events),
        mean_ms=statistics.fmean(intervals) if intervals else None,
        cv=_coefficient_of_variation(intervals),
        repeated_interval_ratio=repeated,
    )


def _pointer(events: list[BehaviorEvent]) -> PointerKinematics | None:
    points: list[tuple[float, float, float]] = []
    for event in events:
        x = _number(event.data.get("x"))
        y = _number(event.data.get("y"))
        if x is not None and y is not None:
            points.append((x, y, event.since_navigation_ms))
    if len(points) < 2:
        return None

    distances: list[float] = []
    speeds: list[float] = []
    vectors: list[tuple[float, float]] = []
    pauses = 0
    for previous, current in pairwise(points):
        dx, dy = current[0] - previous[0], current[1] - previous[1]
        elapsed_ms = current[2] - previous[2]
        distance = math.hypot(dx, dy)
        distances.append(distance)
        vectors.append((dx, dy))
        if elapsed_ms > 0:
            speeds.append(distance * 1000 / elapsed_ms)
        if elapsed_ms >= 80:
            pauses += 1

    accelerations: list[float] = []
    for index in range(1, len(speeds)):
        elapsed_ms = points[index + 1][2] - points[index][2]
        if elapsed_ms > 0:
            accelerations.append((speeds[index] - speeds[index - 1]) * 1000 / elapsed_ms)
    jerks: list[float] = []
    for index in range(1, len(accelerations)):
        elapsed_ms = points[index + 1][2] - points[index][2]
        if elapsed_ms > 0:
            jerks.append((accelerations[index] - accelerations[index - 1]) * 1000 / elapsed_ms)

    angles = [math.atan2(vector[1], vector[0]) for vector in vectors if vector != (0.0, 0.0)]
    turns = [_angle_delta(current, previous) for previous, current in pairwise(angles)]
    direction_changes = sum(abs(turn) >= math.pi / 6 for turn in turns)
    distance = sum(distances)
    displacement = math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1])
    duration = max(0.0, points[-1][2] - points[0][2])
    return PointerKinematics(
        samples=len(points),
        duration_ms=duration,
        distance_px=distance,
        displacement_px=displacement,
        path_efficiency=displacement / distance if distance else 1.0,
        mean_speed_px_s=statistics.fmean(speeds) if speeds else 0.0,
        peak_speed_px_s=max(speeds, default=0.0),
        speed_cv=_coefficient_of_variation(speeds),
        acceleration_cv=_coefficient_of_variation([abs(value) for value in accelerations]),
        mean_abs_jerk_px_s3=statistics.fmean(abs(value) for value in jerks) if jerks else None,
        direction_changes=direction_changes,
        curvature_mean=statistics.fmean(abs(value) for value in turns) if turns else None,
        pause_ratio=pauses / max(1, len(points) - 1),
    )


def _angle_delta(current: float, previous: float) -> float:
    return (current - previous + math.pi) % (2 * math.pi) - math.pi


def _click_target_misses(events: list[BehaviorEvent]) -> int:
    misses = 0
    for event in events:
        if event.event_type != "click":
            continue
        x, y = _number(event.data.get("x")), _number(event.data.get("y"))
        rect = event.data.get("targetRect")
        if x is None or y is None or not isinstance(rect, dict):
            continue
        left, top = _number(rect.get("left")), _number(rect.get("top"))
        width, height = _number(rect.get("width")), _number(rect.get("height"))
        if None in {left, top, width, height}:
            continue
        assert left is not None and top is not None and width is not None and height is not None
        if not (left <= x <= left + width and top <= y <= top + height):
            misses += 1
    return misses


def _keyboard(events: list[BehaviorEvent]) -> KeyboardFeatures:
    downs: dict[str, list[float]] = {}
    dwell: list[float] = []
    corrections = 0
    repeats = 0
    for event in events:
        family = str(event.data.get("codeFamily") or event.data.get("category") or "unknown")
        if event.event_type == "keydown":
            downs.setdefault(family, []).append(event.since_navigation_ms)
            repeats += bool(event.data.get("repeat"))
            corrections += event.data.get("category") == "control"
        elif event.event_type == "keyup" and downs.get(family):
            started = downs[family].pop(0)
            if event.since_navigation_ms >= started:
                dwell.append(event.since_navigation_ms - started)
    return KeyboardFeatures(
        cadence=_cadence(events),
        paired_keys=len(dwell),
        mean_dwell_ms=statistics.fmean(dwell) if dwell else None,
        dwell_cv=_coefficient_of_variation(dwell),
        zero_dwell_ratio=(sum(value <= 2 for value in dwell) / len(dwell)) if dwell else None,
        correction_count=corrections,
        repeat_count=repeats,
    )


def _scroll(events: list[BehaviorEvent]) -> ScrollFeatures:
    deltas = [
        value
        for event in events
        if (value := _number(event.data.get("deltaY"))) is not None and value != 0
    ]
    fixed_ratio = None
    reversals = 0
    decay_ratio = None
    if deltas:
        rounded = [round(abs(value), 1) for value in deltas]
        fixed_ratio = max(rounded.count(value) for value in set(rounded)) / len(rounded)
        reversals = sum(previous * current < 0 for previous, current in pairwise(deltas))
        decays = [abs(current) < abs(previous) for previous, current in pairwise(deltas)]
        decay_ratio = sum(decays) / len(decays) if decays else None
    intervals = [
        current.since_navigation_ms - previous.since_navigation_ms
        for previous, current in pairwise(events)
        if current.since_navigation_ms >= previous.since_navigation_ms
    ]
    frame_aligned = None
    if intervals:
        frame_aligned = sum(
            min(
                abs(value - 16.667 * round(value / 16.667)),
                abs(value - 8.333 * round(value / 8.333)),
            )
            <= 0.35
            for value in intervals
        ) / len(intervals)
    return ScrollFeatures(_cadence(events), fixed_ratio, reversals, decay_ratio, frame_aligned)


def _contacts(events: list[BehaviorEvent], pointer_type: str) -> ContactFeatures:
    selected = [event for event in events if event.data.get("pointerType") == pointer_type]
    pressure = [
        value for event in selected if (value := _number(event.data.get("pressure"))) is not None
    ]
    geometry = [
        width * height
        for event in selected
        if (width := _number(event.data.get("width"))) is not None
        and (height := _number(event.data.get("height"))) is not None
    ]
    tilt = [
        math.hypot(x, y)
        for event in selected
        if (x := _number(event.data.get("tiltX"))) is not None
        and (y := _number(event.data.get("tiltY"))) is not None
    ]
    active: set[str] = set()
    breaks = 0
    for event in selected:
        pointer_id = str(event.data.get("pointerId", "unknown"))
        if event.event_type == "pointerdown":
            if pointer_id in active:
                breaks += 1
            active.add(pointer_id)
        elif event.event_type in {"pointerup", "pointercancel"}:
            if pointer_id not in active:
                breaks += 1
            active.discard(pointer_id)
        elif (
            event.event_type == "pointermove"
            and pointer_id not in active
            and (_number(event.data.get("pressure")) or 0) > 0
        ):
            breaks += 1
    moves = [event for event in selected if event.event_type == "pointermove"]
    zero_moves = sum((_number(event.data.get("pressure")) or 0) == 0 for event in moves)
    return ContactFeatures(
        samples=len(selected),
        pointer_ids=len({str(event.data.get("pointerId")) for event in selected}),
        pressure_cv=_coefficient_of_variation(pressure),
        geometry_cv=_coefficient_of_variation(geometry),
        zero_pressure_move_ratio=zero_moves / len(moves) if moves else None,
        tilt_samples=len(tilt),
        tilt_cv=_coefficient_of_variation(tilt),
        continuity_breaks=breaks,
    )


def extract_behavior_features(events: list[BehaviorEvent]) -> BehaviorFeatures:
    ordered = sorted(events, key=lambda item: (item.since_navigation_ms, item.sequence))
    pointer_events = [
        event for event in ordered if event.event_type in {"pointermove", "mousemove"}
    ]
    keyboard_events = [event for event in ordered if event.event_type in {"keydown", "keyup"}]
    scroll_events = [event for event in ordered if event.event_type in {"wheel", "scroll"}]
    return BehaviorFeatures(
        pointer=_pointer(pointer_events),
        keyboard=_keyboard(keyboard_events),
        scroll=_scroll(scroll_events),
        touch=_contacts(ordered, "touch"),
        pen=_contacts(ordered, "pen"),
        click_target_misses=_click_target_misses(ordered),
    )
