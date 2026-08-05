from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "paritylab" / "catalog" / "catalog.json"
MECHANICS = {
    "tls_impersonation": "partial",
    "cdp_instrumentation": "partial",
    "canvas_spoofing": "partial",
    "geometry_spoofing": "planned",
    "audio_spoofing": "partial",
    "synthetic_pointer": "partial",
    "proxy_rotation": "partial",
    "privacy_randomization": "partial",
}


def main() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    mechanics = {item["id"]: item for item in payload["mechanics"]}
    for mechanic_id, expected in MECHANICS.items():
        actual = mechanics[mechanic_id]["status"]
        if actual != expected:
            raise SystemExit(f"{mechanic_id}: expected {expected}, found {actual}")
        mechanics[mechanic_id]["status"] = "implemented"
    behavior = next(item for item in payload["findings"] if item["code"] == "BEHAVIOR_*")
    if behavior["status"] != "partial":
        raise SystemExit(f"BEHAVIOR_* expected partial, found {behavior['status']}")
    behavior["status"] = "implemented"
    CATALOG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
