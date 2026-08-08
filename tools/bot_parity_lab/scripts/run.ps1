param(
    [switch]$Optional,
    [switch]$Headed
)

$ErrorActionPreference = "Stop"
$extras = @("--extra", "browsers")
if ($Optional) {
    $extras += @("--extra", "patchright", "--extra", "nodriver", "--extra", "camoufox")
}

& uv sync @extras
& uv run playwright install chromium
if ($Optional) {
    & uv run patchright install chromium
    & uv run python -m camoufox fetch
}

$arguments = @("run-all")
if ($Optional) { $arguments += "--include-optional" }
if ($Headed) { $arguments += "--headed" }
& uv run paritylab @arguments
exit $LASTEXITCODE
