"""Conservative live-risk extraction for the protected playground.

Only positive observations are considered here. Missing probes remain a scoring
concern and cannot block a request while a browser capture is still in flight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from paritylab.models import SessionState


@dataclass(frozen=True, slots=True)
class LiveGateRisk:
    hard_codes: tuple[str, ...] = ()
    medium_codes: tuple[str, ...] = ()


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def assess_live_gate_risk(session: SessionState) -> LiveGateRisk:
    hard: set[str] = set()
    medium: set[str] = set()

    for finding in session.findings:
        if finding.signal_class.value == "hard_automation":
            hard.add(finding.code)
        elif finding.signal_class.value == "medium_suspicious":
            medium.add(finding.code)

    for probe in session.probes:
        values = list(_walk(probe.data))
        strings = [str(value).lower() for _key, value in values if isinstance(value, str)]
        keyed = {key.lower(): value for key, value in values}
        if any("headlesschrome" in value for value in strings):
            hard.add("JS_HEADLESS_UA")
        if any("swiftshader" in value for value in strings):
            hard.add("CAT_WEBGL_SWIFTSHADER")
        if keyed.get("webdriver") is True:
            hard.add("JS_WEBDRIVER_TRUE")
        if probe.realm == "vendor:botd" and keyed.get("bot") is True:
            hard.add("VENDOR_BOTD_AUTOMATION")
        if any(key.startswith("cdc_") for key in keyed):
            hard.add("CDP_AUTOMATION_GLOBALS")
        outer_width = keyed.get("outerwidth")
        outer_height = keyed.get("outerheight")
        if outer_width == 0 or outer_height == 0:
            medium.add("JS_OUTER_DIMENSIONS_ZERO")

    if len({item.ja4 for item in session.tls_fingerprints if item.ja4}) > 1:
        medium.add("TLS_SESSION_PERSONA_DRIFT")
    return LiveGateRisk(tuple(sorted(hard)), tuple(sorted(medium)))
