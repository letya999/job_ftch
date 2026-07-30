$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..\..')
$root = 'results/research_legacy_20260713'
$log = Join-Path $root 'h22_h26_n140_sequential.log'
$control = Get-Content (Join-Path $root 'n140_repaired/h11_seed42_n140.json') -Raw | ConvertFrom-Json
$ids = $control.experiment_manifest.selected_item_ids_hash
$shots = $control.experiment_manifest.shots.snapshot_hash
$experiments = @(
  @{Id='h22'; Graph='config/pipelines/experiment_h22_no_bge.yaml'; Out="$root/n140_h22_no_bge/h22_seed42_n140.json"},
  @{Id='h23'; Graph='config/pipelines/experiment_h23_bge_observe.yaml'; Out="$root/n140_h23_bge_observe/h23_seed42_n140.json"},
  @{Id='h24'; Graph='config/pipelines/experiment_h24_lexical_observe.yaml'; Out="$root/n140_h24_lexical_observe/h24_seed42_n140.json"},
  @{Id='h25'; Graph='config/pipelines/experiment_h25_post_decision_extraction.yaml'; Out="$root/n140_h25_post_decision_extraction/h25_seed42_n140.json"},
  @{Id='h26_completeness'; Graph='config/pipelines/experiment_h26_completeness_gate.yaml'; Out="$root/n140_h26_completeness_gate/h26_completeness_seed42_n140.json"},
  @{Id='h26_lifecycle'; Graph='config/pipelines/experiment_h26_lifecycle_gate.yaml'; Out="$root/n140_h26_lifecycle_gate/h26_lifecycle_seed42_n140.json"},
  @{Id='h26_is_job'; Graph='config/pipelines/experiment_h26_is_job_gate.yaml'; Out="$root/n140_h26_is_job_gate/h26_is_job_seed42_n140.json"},
  @{Id='h26_risk'; Graph='config/pipelines/experiment_h26_risk_gate.yaml'; Out="$root/n140_h26_risk_gate/h26_risk_seed42_n140.json"},
  @{Id='h26_quality'; Graph='config/pipelines/experiment_h26_quality_gate.yaml'; Out="$root/n140_h26_quality_gate/h26_quality_seed42_n140.json"}
)
New-Item -ItemType Directory -Force -Path $root | Out-Null
Add-Content $log "$(Get-Date -Format o) START ids=$ids shots=$shots"
foreach ($e in $experiments) {
  $dir = Split-Path $e.Out
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  if (Test-Path $e.Out) { Add-Content $log "$(Get-Date -Format o) SKIP $($e.Id) existing"; continue }
  Add-Content $log "$(Get-Date -Format o) START $($e.Id)"
  & uv run python scripts/eval/run_pipeline_eval.py --graph $e.Graph --dataset fixtures/dataset/eval_dataset.jsonl --sample 140 --seed 42 --profile-source tenant --tenant-id ai_jobs --user-id 480637186 --state-mode runtime --no-langfuse --expected-selected-item-ids-hash $ids --expected-shot-snapshot-hash $shots --run-name "research_legacy_20260713_$($e.Id)_n140" --out $e.Out 2>&1 | Tee-Object -FilePath $log -Append
  $exit = $LASTEXITCODE
  Add-Content $log "$(Get-Date -Format o) END $($e.Id) exit=$exit"
  if ($exit -ne 0) { Add-Content $log "$(Get-Date -Format o) STOP after $($e.Id)"; break }
}
Add-Content $log "$(Get-Date -Format o) DONE"
