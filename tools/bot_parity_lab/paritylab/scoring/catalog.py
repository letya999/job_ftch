from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

from paritylab.legacy_snapshot import score_snapshot as score_catalog_snapshot

from paritylab.models import (
    Finding,
    GateDisposition,
    JsonValue,
    ScoreSummary,
    SessionState,
    SignalClass,
)
from paritylab.reputation import OfflineIPReputation
from paritylab.scoring.common import (
    CATALOG_SEVERITY_CLASS,
    HARD_WEIGHT,
    LOW_WEIGHT,
    MEDIUM_WEIGHT,
    _catalog_snapshot,
    _deep_get,
    _finding,
    _header_map,
    _light_interaction,
    _light_request,
    _light_window,
    _realm_map,
)

def _catalog_findings(session: SessionState) -> list[Finding]:
    report = score_catalog_snapshot(session.client_name, _catalog_snapshot(session))
    findings: list[Finding] = []
    for item in report.findings:
        signal_class = CATALOG_SEVERITY_CLASS.get(item.severity)
        if signal_class is None:
            continue
        findings.append(
            _finding(
                signal_class,
                item.code if item.code.startswith("CAT_") else f"CATALOG_{item.code}",
                "Red-team catalog signal",
                item.detail,
                evidence={"catalog_severity": item.severity},
            )
        )
    return findings
