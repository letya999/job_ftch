from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paritylab.baselines import audit_baselines, load_baseline_runs, load_profiles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--profiles", type=Path, default=ROOT / "data" / "baseline_profiles.json")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    version, profiles = load_profiles(args.profiles)
    audit = audit_baselines(version, profiles, load_baseline_runs(args.artifacts))
    print(json.dumps(audit.to_json(), indent=2, sort_keys=True))
    return 2 if args.require_complete and not audit.complete else 0


if __name__ == "__main__":
    raise SystemExit(main())
