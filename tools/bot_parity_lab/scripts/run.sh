#!/usr/bin/env bash
set -euo pipefail

optional=0
headed=0
for arg in "$@"; do
  case "$arg" in
    --optional) optional=1 ;;
    --headed) headed=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

extras=(--extra browsers)
if [[ "$optional" == 1 ]]; then
  extras+=(--extra patchright --extra nodriver --extra camoufox)
fi

uv sync "${extras[@]}"
uv run playwright install chromium
if [[ "$optional" == 1 ]]; then
  uv run patchright install chromium
  uv run python -m camoufox fetch
fi

args=()
[[ "$optional" == 1 ]] && args+=(--include-optional)
[[ "$headed" == 1 ]] && args+=(--headed)
exec uv run paritylab run-all "${args[@]}"
