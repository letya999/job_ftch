$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..\..')

$logDir = Join-Path (Get-Location) 'results/research_20260712'
$logPath = Join-Path $logDir 'sequential_runner.log'
$baseline = 'results/research_20260712/h1/00_historical_best_seed42.json'
$baselineGraph = 'config/pipelines/historical_best.yaml'
$dataset = 'fixtures/dataset/eval_dataset.jsonl'
$itemHash = (Get-Content $baseline -Raw | ConvertFrom-Json).experiment_manifest.selected_item_ids_hash
$shotHash = (Get-Content $baseline -Raw | ConvertFrom-Json).experiment_manifest.shots.snapshot_hash

function Log([string]$message) {
    $line = "$(Get-Date -Format o) $message"
    Add-Content -LiteralPath $logPath -Value $line
}

function Invoke-Uv([string[]]$arguments) {
    & uv run python @arguments 2>&1 | Tee-Object -FilePath $logPath -Append | Out-Host
    return [int]$LASTEXITCODE
}

$experiments = @(
    @{ Id='h2'; Graph='config/pipelines/experiment_prefilter_threshold_003.yaml'; Output='results/research_20260712/h2/01_experiment_prefilter_threshold_003_seed42.json' },
    @{ Id='h3'; Graph='config/pipelines/experiment_prefilter_no_rescue.yaml'; Output='results/research_20260712/h3/01_experiment_prefilter_no_rescue_seed42.json' },
    @{ Id='h4'; Graph='config/pipelines/experiment_shadow_garbage.yaml'; Output='results/research_20260712/h4_shadow_garbage.json' },
    @{ Id='h5'; Graph='config/pipelines/experiment_shadow_hard_filter.yaml'; Output='results/research_20260712/h5/01_experiment_shadow_hard_filter_seed42.json' },
    @{ Id='h6'; Graph='config/pipelines/experiment_shadow_dedup.yaml'; Output='results/research_20260712/h6/01_experiment_shadow_dedup_seed42.json' },
    @{ Id='h7'; Graph='config/pipelines/experiment_llm_budget.yaml'; Output='results/research_20260712/h7/01_experiment_llm_budget_seed42.json' },
    @{ Id='h9'; Graph='config/pipelines/experiment_quality_override.yaml'; Output='results/research_20260712/h9/01_experiment_quality_override_seed42.json' },
    @{ Id='h10'; Graph='config/pipelines/experiment_parallel_bgem3.yaml'; Output='results/research_20260712/h10/01_experiment_parallel_bgem3_seed42.json' }
)

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Log 'SEQUENTIAL RESEARCH START'
Log "baseline=$baseline item_hash=$itemHash shot_hash=$shotHash"

foreach ($experiment in $experiments) {
    $id = $experiment.Id
    $graph = $experiment.Graph
    $output = $experiment.Output
    $comparison = Join-Path (Split-Path $output) 'comparison.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $output) | Out-Null

    if (Test-Path $output) {
        Log "$id existing output found; validating and comparing"
    } else {
        Log "$id preflight"
        $preflight = Invoke-Uv @('scripts/eval/run_graph_sweep.py', '--baseline', $baselineGraph, '--candidate', $graph, '--dataset', $dataset, '--seed', '42', '--item-ids-hash', $itemHash, '--shot-snapshot-hash', $shotHash)
        if ($preflight -ne 0) { Log "$id preflight FAILED exit=$preflight"; continue }

        Log "$id run candidate (single process)"
        $run = Invoke-Uv @('scripts/eval/run_pipeline_eval.py', '--graph', $graph, '--dataset', $dataset, '--sample', '400', '--seed', '42', '--profile-source', 'tenant', '--tenant-id', 'ai_jobs', '--user-id', '480637186', '--state-mode', 'runtime', '--no-langfuse', '--expected-selected-item-ids-hash', $itemHash, '--expected-shot-snapshot-hash', $shotHash, '--out', $output)
        if ($run -ne 0 -or -not (Test-Path $output)) { Log "$id run FAILED exit=$run"; continue }
    }

    Log "$id compare"
    $compareOutput = & uv run python scripts/eval/compare_policy_runs.py $baseline $output 2>&1
    $compareExit = $LASTEXITCODE
    $compareOutput | Tee-Object -FilePath $comparison | Tee-Object -FilePath $logPath -Append | Out-Null
    if ($compareExit -ne 0) { Log "$id compare FAILED exit=$compareExit"; continue }
    Log "$id COMPLETE"
}

Log 'H8 intentionally pending: its score window must be chosen from H7 transition distributions, not guessed.'
Log 'SEQUENTIAL RESEARCH STOPPED AFTER H10'
