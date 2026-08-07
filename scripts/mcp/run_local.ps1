# Local foreground MCP server (no Docker). Windows PowerShell / WSL-adjacent host.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

if (-not $env:JOB_FTCH_TRACING_ENABLED) { $env:JOB_FTCH_TRACING_ENABLED = "false" }
if (-not $env:JOB_FTCH_OPENOBSERVE_ENABLED) { $env:JOB_FTCH_OPENOBSERVE_ENABLED = "false" }
if (-not $env:JOB_FTCH_STORE_BACKEND) { $env:JOB_FTCH_STORE_BACKEND = "sqlite" }
if (-not $env:JOB_FTCH_JOB_BACKEND) { $env:JOB_FTCH_JOB_BACKEND = "sqlite" }
if (-not $env:JOB_FTCH_SEARCH_BACKEND) { $env:JOB_FTCH_SEARCH_BACKEND = "sqlite" }
if (-not $env:JOB_FTCH_JOB_GROUP_STORE_BACKEND) { $env:JOB_FTCH_JOB_GROUP_STORE_BACKEND = "sqlite" }
if (-not $env:JOB_FTCH_CONFIGS_DIR) { $env:JOB_FTCH_CONFIGS_DIR = "docker/local-mcp/config/tenants" }
if (-not $env:JOB_FTCH_LLM_BACKEND) { $env:JOB_FTCH_LLM_BACKEND = "openai" }
if (-not $env:JOB_FTCH_OPENAI_BASE_URL) { $env:JOB_FTCH_OPENAI_BASE_URL = "http://127.0.0.1:8317/v1" }

$HostAddr = if ($env:JOB_FTCH_MCP_HOST) { $env:JOB_FTCH_MCP_HOST } else { "127.0.0.1" }
$Port = if ($env:JOB_FTCH_MCP_PORT) { $env:JOB_FTCH_MCP_PORT } else { "8000" }
$Transport = if ($env:JOB_FTCH_MCP_TRANSPORT) { $env:JOB_FTCH_MCP_TRANSPORT } else { "streamable-http" }

uv run job_ftch mcp-server `
  --configs-dir $env:JOB_FTCH_CONFIGS_DIR `
  --transport $Transport `
  --host $HostAddr `
  --port $Port
