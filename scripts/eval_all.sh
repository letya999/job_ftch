#!/usr/bin/env bash
# Run all eval harnesses (TD-002 / ADR-032).
#
# Exits non-zero if any harness reports a regression.
# Use this in CI or as a local pre-merge check.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p artifacts/eval

echo "=== Classification eval (1412 samples) ==="
python scripts/evaluate_classification.py --gate

echo
echo "=== Extraction eval (gold_samples.jsonl) ==="
python scripts/evaluate_extraction.py --gate

echo
echo "All eval harnesses passed."
