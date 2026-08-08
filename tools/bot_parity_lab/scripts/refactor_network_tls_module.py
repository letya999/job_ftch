from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "paritylab" / "scoring" / "network.py"
TLS = ROOT / "paritylab" / "scoring" / "tls.py"
MARKER = "\ndef _tls_findings(session: SessionState) -> list[Finding]:\n"
IMPORT = "from paritylab.scoring.tls import _tls_findings\n"
HEADER = """from __future__ import annotations

import statistics
from itertools import pairwise

from paritylab.models import Finding, JsonValue, SessionState, SignalClass, TLSFingerprint
from paritylab.scoring.common import _finding
"""


def main() -> None:
    source = NETWORK.read_text(encoding="utf-8")
    if IMPORT in source or TLS.exists():
        raise SystemExit("TLS scoring has already been extracted")
    start = source.find(MARKER)
    if start < 0:
        raise SystemExit("TLS scoring boundary not found")
    body = source[start + 1 :]
    insertion = source.find("from paritylab.scoring.common import (")
    if insertion < 0:
        raise SystemExit("network import boundary not found")
    source = source[:insertion] + IMPORT + source[insertion:start].rstrip() + "\n"
    NETWORK.write_text(source, encoding="utf-8")
    TLS.write_text(HEADER + "\n" + body, encoding="utf-8")


if __name__ == "__main__":
    main()
