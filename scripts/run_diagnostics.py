import os
import subprocess

import yaml

sources_file = "debug_sources.yaml"
profile_path = "profiles/ai_jobs_ru_kz.yaml"

with open(sources_file, encoding="utf-8") as f:
    config = yaml.safe_load(f)

sources = config.get("sources", [])

env = os.environ.copy()
env["JOB_FTCH_LOG_LEVEL"] = "DEBUG"
env["JOB_FTCH_STORE_BACKEND"] = "memory"
env["JOB_FTCH_DRY_RUN"] = "True"
env["JOB_FTCH_FILTER_PROFILE_PATH"] = profile_path

for i, source in enumerate(sources):
    source_type = source["type"]
    name = source.get("source_name") or source.get("entity")
    print(f"[{i + 1}/{len(sources)}] Running diagnostic for {source_type}:{name}...")

    cmd = ["python", "-m", "job_ftch", "--source-backend", source_type]
    if source_type == "career_site":
        cmd.extend(["--career-site-url", source["url"]])
    else:
        cmd.extend(["--telegram-entity", source["entity"]])

    cmd.extend(["--max-items", "10"])

    log_file = f"diag_{source_type}_{name}.txt".replace("/", "_")
    with open(log_file, "w", encoding="utf-8") as f:
        subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    print(f"  Logs saved to {log_file}")
