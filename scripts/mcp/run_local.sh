#!/usr/bin/env bash
# Local foreground MCP server (no Docker). Linux / macOS / WSL.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

export JOB_FTCH_TRACING_ENABLED="${JOB_FTCH_TRACING_ENABLED:-false}"
export JOB_FTCH_OPENOBSERVE_ENABLED="${JOB_FTCH_OPENOBSERVE_ENABLED:-false}"
export JOB_FTCH_STORE_BACKEND="${JOB_FTCH_STORE_BACKEND:-sqlite}"
export JOB_FTCH_JOB_BACKEND="${JOB_FTCH_JOB_BACKEND:-sqlite}"
export JOB_FTCH_SEARCH_BACKEND="${JOB_FTCH_SEARCH_BACKEND:-sqlite}"
export JOB_FTCH_JOB_GROUP_STORE_BACKEND="${JOB_FTCH_JOB_GROUP_STORE_BACKEND:-sqlite}"
export JOB_FTCH_CONFIGS_DIR="${JOB_FTCH_CONFIGS_DIR:-docker/local-mcp/config/tenants}"
export JOB_FTCH_LLM_BACKEND="${JOB_FTCH_LLM_BACKEND:-openai}"
export JOB_FTCH_OPENAI_BASE_URL="${JOB_FTCH_OPENAI_BASE_URL:-http://127.0.0.1:8317/v1}"
# Align relevance judge with gateway catalog when OPENAI_MODEL is set.
if [[ -z "${JOB_FTCH_RELEVANCE_LLM_MODEL:-}" && -n "${JOB_FTCH_OPENAI_MODEL:-}" ]]; then
  export JOB_FTCH_RELEVANCE_LLM_MODEL="${JOB_FTCH_OPENAI_MODEL}"
fi

HOST="${JOB_FTCH_MCP_HOST:-127.0.0.1}"
PORT="${JOB_FTCH_MCP_PORT:-8000}"
TRANSPORT="${JOB_FTCH_MCP_TRANSPORT:-streamable-http}"

exec uv run job_ftch mcp-server \
  --configs-dir "$JOB_FTCH_CONFIGS_DIR" \
  --transport "$TRANSPORT" \
  --host "$HOST" \
  --port "$PORT"
