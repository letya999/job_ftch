from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paritylab.oss_registry import load_oss_registry  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate parity-lab OSS component admission")
    parser.add_argument("path", nargs="?", type=Path, default=Path("data/oss_components.json"))
    args = parser.parse_args()
    registry = load_oss_registry(args.path)
    print(f"OSS registry valid: {len(registry.components)} components")


if __name__ == "__main__":
    main()
