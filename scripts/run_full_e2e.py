import os
import subprocess
import time
from pathlib import Path

import yaml

sources_file = "precise_debug_sources.yaml"
profile_path = "config/profiles/ai_jobs_ru_kz.yaml"

with open(sources_file, encoding="utf-8") as f:
    config = yaml.safe_load(f)

sources = config.get("sources", [])

env = os.environ.copy()
if Path(".env.dev").exists():
    with open(".env.dev", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v

env["JOB_FTCH_LOG_LEVEL"] = "DEBUG"
env["JOB_FTCH_STORE_BACKEND"] = "memory"
env["JOB_FTCH_DRY_RUN"] = "True"
env["JOB_FTCH_FILTER_PROFILE_PATH"] = profile_path
# Enable embedding prefilter to see if it helps
env["JOB_FTCH_EMBEDDING_PREFILTER_ENABLED"] = "True"

results = []

for i, source in enumerate(sources):
    source_type = source["type"]
    name = source.get("source_name") or source.get("entity")
    print(f"[{i + 1}/{len(sources)}] Running e2e diagnostic for {source_type}:{name}...")

    cmd = ["python", "-m", "job_ftch", "--source-backend", source_type]
    if source_type == "career_site":
        cmd.extend(["--career-site-url", source["url"]])
    else:
        cmd.extend(["--telegram-entity", source["entity"]])

    cmd.extend(["--max-items", "20"])

    log_file = f"e2e_diag_{source_type}_{name}.txt".replace("/", "_")

    start_time = time.time()
    with open(log_file, "w", encoding="utf-8") as f:
        process = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    duration = time.time() - start_time

    status = "SUCCESS" if process.returncode == 0 else f"FAILED({process.returncode})"
    print(f"  Done in {duration:.1f}s. Status: {status}. Logs: {log_file}")
    results.append((name, status, duration, log_file))

print("\n--- Diagnostic Summary ---")
for name, status, dur, log in results:
    print(f"{name:30} | {status:10} | {dur:6.1f}s | {log}")
