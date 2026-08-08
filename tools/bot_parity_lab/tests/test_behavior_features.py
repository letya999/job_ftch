from __future__ import annotations

from paritylab.behavior_features import extract_behavior_features
from paritylab.models import BehaviorEvent


def _event(sequence: int, kind: str, at: float, **data: object) -> BehaviorEvent:
    return BehaviorEvent(
        session_id="behavior",
        observed_at="2026-08-05T00:00:00Z",
        sequence=sequence,
        event_type=kind,
        client_ts_ms=at,
        since_navigation_ms=at,
        trusted=True,
        data=data,  # type: ignore[arg-type]
    )


def test_extracts_pointer_kinematics() -> None:
    events = [_event(index, "pointermove", index * 10.0, x=index * 10, y=0) for index in range(12)]
    features = extract_behavior_features(events)
    assert features.pointer is not None
    assert features.pointer.samples == 12
    assert features.pointer.path_efficiency == 1.0
    assert features.pointer.direction_changes == 0
    assert features.pointer.speed_cv == 0.0


def test_extracts_scroll_quantization_and_click_target_miss() -> None:
    events = [_event(index, "wheel", index * 20.0, deltaY=100) for index in range(8)]
    events.append(
        _event(
            9,
            "click",
            200,
            x=500,
            y=500,
            targetRect={"left": 0, "top": 0, "width": 100, "height": 100},
        )
    )
    features = extract_behavior_features(events)
    assert features.scroll.fixed_delta_ratio == 1.0
    assert features.scroll.cadence.cv == 0.0
    assert features.click_target_misses == 1


def test_extracts_keyboard_dwell_and_touch_continuity() -> None:
    events = []
    sequence = 0
    for index in range(4):
        events.append(
            _event(sequence, "keydown", index * 20, codeFamily="Key", category="printable")
        )
        sequence += 1
        events.append(
            _event(sequence, "keyup", index * 20 + 1, codeFamily="Key", category="printable")
        )
        sequence += 1
    for index in range(10):
        events.append(
            _event(
                sequence,
                "pointermove",
                100 + index * 10,
                pointerType="touch",
                pointerId=7,
                pressure=0.5,
                width=12,
                height=12,
                x=index,
                y=index,
            )
        )
        sequence += 1
    features = extract_behavior_features(events)
    assert features.keyboard.paired_keys == 4
    assert features.keyboard.zero_dwell_ratio == 1.0
    assert features.touch.samples == 10
    assert features.touch.pressure_cv == 0.0
    assert features.touch.geometry_cv == 0.0
    assert features.touch.continuity_breaks == 10


def test_extracts_scroll_frame_alignment_and_decay() -> None:
    deltas = [120, 96, 72, 48, 32, 20, 12, 6]
    events = [
        _event(index, "wheel", index * 16.667, deltaY=delta) for index, delta in enumerate(deltas)
    ]
    features = extract_behavior_features(events)
    assert features.scroll.frame_aligned_ratio == 1.0
    assert features.scroll.decay_ratio == 1.0
    assert features.scroll.direction_reversals == 0
