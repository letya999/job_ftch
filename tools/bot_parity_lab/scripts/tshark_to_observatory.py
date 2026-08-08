from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paritylab.capture_adapters.tshark import tcpip_observation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert TShark JSON to parity-lab evidence")
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    payload = tcpip_observation(json.loads(args.capture.read_text(encoding="utf-8")))
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
