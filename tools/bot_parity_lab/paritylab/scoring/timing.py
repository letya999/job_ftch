from __future__ import annotations

from collections.abc import Mapping

from paritylab.models import Finding, SessionState, SignalClass
from paritylab.scoring.common import _deep_get, _finding, _realm_map


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


def _timing_findings(session: SessionState) -> list[Finding]:
    realms = _realm_map(session)
    timing = _deep_get(realms.get("deep", {}), "timing", {})
    if not isinstance(timing, Mapping) or not timing:
        return [
            _finding(
                SignalClass.LOW,
                "TIMING_PROBE_MISSING",
                "Event-loop timing evidence missing",
                "Clock resolution, task ordering, animation cadence and timeout delay were not reported.",
                realms=["deep"],
            )
        ]
    findings: list[Finding] = []
    violations = timing.get("monotonicViolations")
    if isinstance(violations, int) and violations > 0:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TIMING_MONOTONIC_REGRESSION",
                "performance.now regressed",
                "The monotonic browser clock moved backwards during a tight sampling window.",
                evidence={"violation_count": violations},
                realms=["deep"],
            )
        )
    drift = _finite_number(timing.get("dateDriftMs"))
    drift_range = _finite_number(timing.get("dateDriftRangeMs"))
    if drift is not None and abs(drift) > 100:
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TIMING_CLOCK_ORIGIN_CONFLICT",
                "Date and performance clocks disagree",
                "Date.now differs materially from performance.timeOrigin plus performance.now.",
                evidence={"date_drift_ms": round(drift, 3)},
                realms=["deep"],
            )
        )
    if drift_range is not None and drift_range > 25:
        findings.append(
            _finding(
                SignalClass.LOW,
                "TIMING_CLOCK_DRIFT_UNSTABLE",
                "Cross-clock drift is unstable",
                "Repeated Date/performance comparisons vary more than expected in one microtask sequence.",
                evidence={"drift_range_ms": round(drift_range, 3)},
                realms=["deep"],
            )
        )
    order = timing.get("taskOrder")
    if isinstance(order, list):
        positions = {str(name): index for index, name in enumerate(order)}
        tasks = [positions[name] for name in ("message", "timeout") if name in positions]
        microtasks = [positions[name] for name in ("promise", "microtask") if name in positions]
        if tasks and microtasks and max(microtasks) > min(tasks):
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "TIMING_TASK_ORDER_CONFLICT",
                    "Microtask and task ordering is inconsistent",
                    "A Promise or queueMicrotask callback ran after the first MessageChannel/setTimeout task.",
                    evidence={"task_order": [str(item) for item in order]},
                    realms=["deep"],
                )
            )
    raf = timing.get("rafIntervals")
    if isinstance(raf, list) and any((_finite_number(value) or 0) <= 0 for value in raf):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TIMING_RAF_NON_MONOTONIC",
                "Animation-frame timestamps are non-monotonic",
                "One or more requestAnimationFrame intervals are zero or negative.",
                realms=["deep"],
            )
        )
    resolution = _finite_number(timing.get("nowResolutionMs"))
    if resolution is not None and resolution >= 1:
        findings.append(
            _finding(
                SignalClass.INFO,
                "TIMING_COARSE_RESOLUTION",
                "High-resolution timer is coarsened",
                "performance.now resolution is at least one millisecond; privacy and isolation policy commonly cause this.",
                evidence={"resolution_ms": round(resolution, 6)},
                realms=["deep"],
            )
        )
    deep_origin = _finite_number(timing.get("timeOrigin"))
    window_origin = _finite_number(_deep_get(realms.get("window", {}), "performance.timeOrigin"))
    if (
        deep_origin is not None
        and window_origin is not None
        and abs(deep_origin - window_origin) > 5
    ):
        findings.append(
            _finding(
                SignalClass.MEDIUM,
                "TIMING_REALM_ORIGIN_DRIFT",
                "Window probes disagree on time origin",
                "Primary and deep probes from the same window report different performance.timeOrigin values.",
                evidence={"difference_ms": round(abs(deep_origin - window_origin), 3)},
                realms=["window", "deep"],
            )
        )
    return findings
