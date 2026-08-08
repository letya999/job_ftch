from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping
from itertools import pairwise

from paritylab.behavior_features import extract_behavior_features
from paritylab.models import (
    Finding,
    JsonValue,
    SessionState,
    SignalClass,
)
from paritylab.scoring.common import (
    _deep_get,
    _finding,
    _realm_map,
)


def _behavior_findings(session: SessionState) -> list[Finding]:
    findings: list[Finding] = []
    recorded_events = sorted(session.behavior, key=lambda item: item.sequence)
    events = sorted(recorded_events, key=lambda item: (item.since_navigation_ms, item.sequence))
    if not events:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_NO_EVENTS",
                "No interaction events",
                "No mouse, pointer, keyboard, scroll, focus, or visibility events were observed.",
            )
        )
        return findings

    features = extract_behavior_features(events)
    replay = session.metadata.get("behavior_replay")
    if isinstance(replay, Mapping) and replay.get("eligible") is True:
        similarity = replay.get("nearest_similarity")
        matched = replay.get("matched_session_hash")
        if replay.get("exact_match") is True and matched:
            findings.append(
                _finding(
                    SignalClass.HARD_BOT,
                    "BEHAVIOR_EXACT_REPLAY",
                    "Behavior sequence exactly replays another session",
                    "The privacy-safe event, timing and geometry signature exactly matches a distinct prior session.",
                    evidence={
                        "similarity": 1.0,
                        "matched_session_hash": str(matched),
                        "signature_prefix": str(replay.get("signature_prefix", "")),
                    },
                )
            )
        elif isinstance(similarity, (int, float)) and similarity >= 0.94 and matched:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "BEHAVIOR_NEAR_REPLAY",
                    "Behavior sequence closely matches another session",
                    "The privacy-safe sequence signature is unusually similar to a distinct prior session.",
                    evidence={
                        "similarity": round(float(similarity), 6),
                        "matched_session_hash": str(matched),
                        "signature_prefix": str(replay.get("signature_prefix", "")),
                    },
                )
            )

    backwards = [
        (previous, current)
        for previous, current in pairwise(recorded_events)
        if current.since_navigation_ms + 1 < previous.since_navigation_ms
    ]
    if backwards:
        previous, current = backwards[0]
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_TIMESTAMP_REGRESSION",
                "Interaction timestamps regress",
                "The recorded event sequence moves backwards in page-monotonic time, which is inconsistent with a single browser event stream.",
                evidence={
                    "regression_count": len(backwards),
                    "previous_sequence": previous.sequence,
                    "previous_ms": round(previous.since_navigation_ms, 3),
                    "current_sequence": current.sequence,
                    "current_ms": round(current.since_navigation_ms, 3),
                },
            )
        )

    trusted_values = [event.trusted for event in events if event.trusted is not None]
    if trusted_values and not any(trusted_values):
        findings.append(
            _finding(
                SignalClass.HARD_BOT,
                "BEHAVIOR_ALL_UNTRUSTED",
                "All interaction events are untrusted",
                "Every captured DOM interaction event has isTrusted=false, indicating synthetic JavaScript dispatch.",
                evidence={"event_count": len(trusted_values)},
            )
        )

    event_types = Counter(event.event_type for event in events)
    if not any(name in event_types for name in ("pointermove", "mousemove")):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_NO_POINTER_PATH",
                "No pointer movement path",
                "The session reached completion without any mouse or pointer movement samples.",
                evidence={"event_types": dict(event_types)},
            )
        )
    if "click" not in event_types:
        findings.append(
            _finding(
                SignalClass.LOW,
                "BEHAVIOR_NO_CLICK",
                "No click event",
                "No user click was observed during the dwell period.",
                evidence={"event_types": dict(event_types)},
            )
        )
    if "scroll" not in event_types:
        findings.append(
            _finding(
                SignalClass.LOW,
                "BEHAVIOR_NO_SCROLL",
                "No scroll event",
                "The page contains enough content to scroll, but no scroll event was observed.",
            )
        )

    action_events = [
        event
        for event in events
        if event.event_type in {"pointerdown", "mousedown", "keydown", "click", "scroll"}
    ]
    if action_events and action_events[0].since_navigation_ms < 80:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_FIRST_ACTION_TOO_FAST",
                "First action occurred immediately",
                "The first meaningful interaction happened less than 80 ms after navigation start.",
                evidence={"first_action_ms": round(action_events[0].since_navigation_ms, 3)},
            )
        )

    move_events = [event for event in events if event.event_type in {"pointermove", "mousemove"}]
    meaningful_events = [
        event
        for event in recorded_events
        if event.event_type
        in {
            "pointermove",
            "mousemove",
            "pointerdown",
            "pointerup",
            "mousedown",
            "mouseup",
            "click",
            "wheel",
            "scroll",
            "keydown",
            "keyup",
        }
    ]
    if len(meaningful_events) >= 8:
        compressed_windows = []
        for index in range(len(meaningful_events) - 7):
            window = meaningful_events[index : index + 8]
            elapsed = window[-1].since_navigation_ms - window[0].since_navigation_ms
            distinct_types = {event.event_type for event in window}
            if elapsed >= 0 and elapsed <= 4 and len(distinct_types) >= 3:
                compressed_windows.append((window, elapsed))
        if compressed_windows:
            window, elapsed = compressed_windows[0]
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "BEHAVIOR_EVENT_BURST_COMPRESSED",
                    "Interaction burst is implausibly compressed",
                    "Eight or more distinct interaction records were emitted inside a four-millisecond page-time window.",
                    evidence={
                        "event_count": len(window),
                        "elapsed_ms": round(elapsed, 3),
                        "event_types": [event.event_type for event in window],
                    },
                )
            )

    pointer_teleports: list[dict[str, JsonValue]] = []
    for previous, current in pairwise(move_events):
        previous_x, previous_y = previous.data.get("x"), previous.data.get("y")
        current_x, current_y = current.data.get("x"), current.data.get("y")
        if not all(
            isinstance(value, (int, float))
            for value in (previous_x, previous_y, current_x, current_y)
        ):
            continue
        elapsed = current.since_navigation_ms - previous.since_navigation_ms
        distance = math.hypot(
            float(current_x) - float(previous_x), float(current_y) - float(previous_y)
        )
        if 0 <= elapsed <= 3 and distance >= 500:
            pointer_teleports.append(
                {
                    "from_sequence": previous.sequence,
                    "to_sequence": current.sequence,
                    "elapsed_ms": round(elapsed, 3),
                    "distance_px": round(distance, 3),
                }
            )
    if len(pointer_teleports) >= 2:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_POINTER_TELEPORTS",
                "Pointer path contains repeated teleports",
                "Multiple large pointer jumps occurred inside three milliseconds; this is not a stable physical input trajectory.",
                evidence={"teleports": pointer_teleports[:4]},
            )
        )

    if len(move_events) >= 6:
        intervals = [
            move_events[index].since_navigation_ms - move_events[index - 1].since_navigation_ms
            for index in range(1, len(move_events))
        ]
        rounded = [round(value, 1) for value in intervals if value >= 0]
        if len(rounded) >= 5:
            mean = statistics.fmean(rounded)
            deviation = statistics.pstdev(rounded)
            if mean > 0 and deviation / mean < 0.08:
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "BEHAVIOR_POINTER_CADENCE_REGULAR",
                        "Pointer cadence is highly regular",
                        "Pointer-move intervals have unusually low variation.",
                        evidence={
                            "samples": len(rounded),
                            "mean_ms": round(mean, 3),
                            "stddev_ms": round(deviation, 3),
                        },
                    )
                )

        points: list[tuple[float, float]] = []
        for event in move_events:
            x = event.data.get("x")
            y = event.data.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                points.append((float(x), float(y)))
        if len(points) >= 6:
            segment_angles: list[float] = []
            for index in range(1, len(points)):
                dx = points[index][0] - points[index - 1][0]
                dy = points[index][1] - points[index - 1][1]
                if dx or dy:
                    segment_angles.append(math.atan2(dy, dx))
            if len(segment_angles) >= 5 and statistics.pstdev(segment_angles) < 0.02:
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "BEHAVIOR_POINTER_LINEAR",
                        "Pointer path is nearly perfectly linear",
                        "The captured movement path has almost no angular variation.",
                        evidence={"angle_stddev": round(statistics.pstdev(segment_angles), 6)},
                    )
                )

    realms = _realm_map(session)
    window = realms.get("window", {})
    final_window = realms.get("window-final", {})
    had_click = event_types.get("click", 0) > 0
    active = _deep_get(
        final_window,
        "userActivation.hasBeenActive",
        _deep_get(window, "userActivation.hasBeenActive"),
    )
    if had_click and active is False:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_USER_ACTIVATION_CONFLICT",
                "Click does not match userActivation",
                "A click was recorded but navigator.userActivation.hasBeenActive remained false.",
                realms=["window"],
            )
        )
    pointer = features.pointer
    if (
        pointer is not None
        and pointer.samples >= 10
        and pointer.path_efficiency >= 0.995
        and pointer.direction_changes == 0
        and (pointer.speed_cv or 0.0) < 0.12
    ):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_POINTER_BALLISTIC_TEMPLATE",
                "Pointer path follows a ballistic template",
                "The trajectory combines near-perfect path efficiency, no corrective turns and low speed variation.",
                evidence={
                    "samples": pointer.samples,
                    "path_efficiency": round(pointer.path_efficiency, 5),
                    "direction_changes": pointer.direction_changes,
                    "speed_cv": round(pointer.speed_cv or 0.0, 5),
                },
            )
        )
    keyboard = features.keyboard
    if keyboard.cadence.samples >= 8 and (keyboard.cadence.cv or 1.0) < 0.08:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_KEYBOARD_CADENCE_REGULAR",
                "Keyboard cadence is highly regular",
                "Keyboard event intervals have implausibly low variation for the observed sequence.",
                evidence={
                    "samples": keyboard.cadence.samples,
                    "cadence_cv": round(keyboard.cadence.cv or 0.0, 5),
                    "repeated_interval_ratio": round(keyboard.cadence.repeated_interval_ratio, 5),
                },
            )
        )
    if keyboard.paired_keys >= 4 and (keyboard.zero_dwell_ratio or 0.0) >= 0.75:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_KEYBOARD_ZERO_DWELL",
                "Key presses have near-zero dwell time",
                "Most paired keydown/keyup events are separated by two milliseconds or less.",
                evidence={
                    "paired_keys": keyboard.paired_keys,
                    "zero_dwell_ratio": round(keyboard.zero_dwell_ratio or 0.0, 5),
                    "mean_dwell_ms": round(keyboard.mean_dwell_ms or 0.0, 3),
                },
            )
        )
    scroll = features.scroll
    if (
        scroll.fixed_delta_ratio is not None
        and scroll.cadence.samples >= 8
        and scroll.fixed_delta_ratio >= 0.9
        and (scroll.cadence.cv or 1.0) < 0.1
    ):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_SCROLL_QUANTIZED",
                "Scroll sequence is mechanically quantized",
                "Nearly every scroll uses the same delta and a highly regular cadence.",
                evidence={
                    "samples": scroll.cadence.samples,
                    "fixed_delta_ratio": round(scroll.fixed_delta_ratio, 5),
                    "cadence_cv": round(scroll.cadence.cv or 0.0, 5),
                },
            )
        )
    if scroll.cadence.samples >= 8 and (scroll.frame_aligned_ratio or 0.0) >= 0.9:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_SCROLL_FRAME_LOCKED",
                "Scroll events are locked to exact frame intervals",
                "Nearly all scroll intervals align within 0.35 ms of 60 Hz or 120 Hz frame boundaries.",
                evidence={
                    "samples": scroll.cadence.samples,
                    "frame_aligned_ratio": round(scroll.frame_aligned_ratio or 0.0, 5),
                    "direction_reversals": scroll.direction_reversals,
                    "decay_ratio": round(scroll.decay_ratio or 0.0, 5),
                },
            )
        )
    for pointer_type, contact in (("touch", features.touch), ("pen", features.pen)):
        if contact.samples >= 8 and contact.continuity_breaks >= 2:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "BEHAVIOR_CONTACT_CONTINUITY_BROKEN",
                    "Touch or pen contact lifecycle is inconsistent",
                    "Pointer identifiers move, end, or restart without a coherent contact lifecycle.",
                    evidence={
                        "pointer_type": pointer_type,
                        "samples": contact.samples,
                        "pointer_ids": contact.pointer_ids,
                        "continuity_breaks": contact.continuity_breaks,
                    },
                )
            )
        if (
            contact.samples >= 10
            and contact.pressure_cv is not None
            and contact.pressure_cv < 0.01
            and (contact.geometry_cv is None or contact.geometry_cv < 0.01)
        ):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "BEHAVIOR_CONTACT_SHAPE_STATIC",
                    "Touch or pen shape is mechanically static",
                    "Pressure and contact geometry remain effectively constant across a long gesture.",
                    evidence={
                        "pointer_type": pointer_type,
                        "samples": contact.samples,
                        "pressure_cv": round(contact.pressure_cv, 5),
                        "geometry_cv": round(contact.geometry_cv or 0.0, 5),
                    },
                )
            )
    if features.click_target_misses:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "BEHAVIOR_CLICK_TARGET_MISMATCH",
                "Click coordinates miss the reported target",
                "One or more click coordinates fall outside the target rectangle captured for the same event.",
                evidence={"miss_count": features.click_target_misses},
            )
        )
    return findings
