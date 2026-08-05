"""One-shot mechanical split of the legacy scoring module.

Kept in-tree as a reproducible migration receipt. It refuses to run after the
split, so it cannot overwrite the package accidentally.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "paritylab" / "scoring.py"
TARGET = ROOT / "paritylab" / "scoring"

GROUPS = {
    "common.py": (
        "_finding",
        "_deep_get",
        "_realm_map",
        "_header_map",
        "_light_path",
        "_light_headers",
        "_light_request",
        "_probe_runtime",
        "_light_user_agent_data",
        "_light_window",
        "_light_interaction",
        "_catalog_snapshot",
    ),
    "catalog.py": ("_catalog_findings",),
    "network.py": ("_network_findings", "_tls_findings"),
    "runtime.py": ("_runtime_findings",),
    "integrity.py": ("_header_digest", "_language_family", "_session_integrity_findings"),
    "realm.py": ("_notification_permission_api_state", "_cross_realm_findings"),
    "behavior.py": ("_behavior_findings",),
    "protocol.py": ("_protocol_and_reputation_findings",),
    "playground.py": ("_playground_findings",),
    "engine.py": ("score_session",),
}

EXTRA_HEADERS = {
    "engine.py": """from paritylab.scoring.behavior import _behavior_findings
from paritylab.scoring.catalog import _catalog_findings
from paritylab.scoring.integrity import _session_integrity_findings
from paritylab.scoring.network import _network_findings, _tls_findings
from paritylab.scoring.playground import _playground_findings
from paritylab.scoring.protocol import _protocol_and_reputation_findings
from paritylab.scoring.realm import _cross_realm_findings
from paritylab.scoring.runtime import _runtime_findings

""",
}

HEADER = """from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

from tools.bot_parity_lab.scoring import score_snapshot as score_catalog_snapshot

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

"""


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit("legacy scoring.py is already split")
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = {name for names in GROUPS.values() for name in names}
    missing = expected - functions.keys()
    if missing:
        raise SystemExit(f"missing expected scoring functions: {sorted(missing)}")

    TARGET.mkdir()
    common_constants = """HARD_WEIGHT = 40
MEDIUM_WEIGHT = 15
LOW_WEIGHT = 4

CATALOG_SEVERITY_CLASS = {
    "high": SignalClass.HARD_BOT,
    "medium": SignalClass.MEDIUM,
    "low": SignalClass.LOW,
}

"""
    for filename, names in GROUPS.items():
        chunks = [ast.get_source_segment(text, functions[name]) or "" for name in names]
        header = HEADER
        if filename == "common.py":
            header = HEADER.split("from paritylab.scoring.common import", 1)[0]
            header += common_constants
        header += EXTRA_HEADERS.get(filename, "")
        (TARGET / filename).write_text(header + "\n\n".join(chunks) + "\n", encoding="utf-8")

    (TARGET / "__init__.py").write_text(
        '"""Explainable, domain-partitioned parity scoring."""\n\n'
        "from paritylab.scoring.engine import score_session\n\n"
        '__all__ = ["score_session"]\n',
        encoding="utf-8",
    )
    SOURCE.unlink()


if __name__ == "__main__":
    main()
