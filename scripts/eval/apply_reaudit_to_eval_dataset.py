"""Apply 400-sample re-audit labels back into the single eval dataset."""

from __future__ import annotations

import json
from pathlib import Path

DATASET = Path("fixtures/dataset/eval_dataset.jsonl")
REAUDIT = Path("fixtures/dataset/eval_400_reaudit.jsonl")


def main() -> None:
    if not DATASET.exists():
        raise SystemExit(f"Dataset not found: {DATASET}")
    if not REAUDIT.exists():
        raise SystemExit(f"Reaudit file not found: {REAUDIT}")

    overrides: dict[str, dict[str, object]] = {}
    for line in REAUDIT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        overrides[str(row["stable_id"])] = row

    updated = 0
    output_lines: list[str] = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        override = overrides.get(str(row.get("stable_id", "")))
        if override is not None:
            row["relevant"] = int(override["new_label"])  # type: ignore
            row["labeler"] = "gpt-4.1-mini-reaudit"
            row["reason"] = str(override.get("justification", "")).strip()
            updated += 1
        output_lines.append(json.dumps(row, ensure_ascii=False))

    DATASET.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    print(f"updated={updated} dataset={DATASET} overrides={len(overrides)}")


if __name__ == "__main__":
    main()
