"""One-shot dev environment bootstrap for job_ftch.

Steps:
  1. build_keywords_from_shots.py  — local keyword extraction (no LLM)
  2. seed_all_shots.py             — embed shots into Qdrant with BGE-M3
  3. gen_ontology.py               — LLM-derived ontology from shots
  4. gen_relevance_prompt.py       — LLM-generated relevance system prompt

Usage:
    uv run python scripts/bootstrap_dev.py
    uv run python scripts/bootstrap_dev.py --shots-only   # skip LLM steps
    uv run python scripts/bootstrap_dev.py --check-only   # print env checklist
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent


def check_env():
    vars_to_check = [
        "JOB_FTCH_QDRANT_URL",
        "JOB_FTCH_OPENAI_API_KEY",
        "JOB_FTCH_OPENAI_MODEL",
        "JOB_FTCH_STORE_DSN",
        "JOB_FTCH_TELEGRAM_API_ID",
        "JOB_FTCH_TELEGRAM_API_HASH",
        "JOB_FTCH_AUTH_TELEGRAM_BOT_TOKEN",
    ]
    for var in vars_to_check:
        val = os.environ.get(var)
        if var == "JOB_FTCH_QDRANT_URL" and not val:
            print(f"{var}: Not Set (default: http://localhost:6333)")
        elif var == "JOB_FTCH_OPENAI_MODEL" and not val:
            print(f"{var}: Not Set (default shown)")
        else:
            print(f"{var}: {'Set' if val else 'Not Set'}")


def run_step(step_num: int, total_steps: int, description: str, script_path: str):
    print(f"\n[STEP {step_num}/{total_steps}] {description}")
    try:
        subprocess.run([sys.executable, script_path], check=True, env=os.environ, cwd=str(ROOT))
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running {script_path}: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: {script_path} not found.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="One-shot dev environment bootstrap")
    parser.add_argument("--shots-only", action="store_true", help="skip LLM steps")
    parser.add_argument("--check-only", action="store_true", help="print env checklist")
    args = parser.parse_args()

    # 0. Load env files at startup
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.dev", override=True)

    if args.check_only:
        check_env()
        sys.exit(0)

    openai_key = os.environ.get("JOB_FTCH_OPENAI_API_KEY")
    skip_llm = args.shots_only
    if not openai_key and not skip_llm:
        print("Warning: JOB_FTCH_OPENAI_API_KEY is not set. LLM steps will be skipped.")
        skip_llm = True

    # Run Steps
    step1_ok = False
    step2_ok = False
    step3_ok = False
    step4_ok = False

    # Step 1
    script_path = str(ROOT / "scripts/shots/build_keywords_from_shots.py")
    step1_ok = run_step(1, 4, "build_keywords_from_shots.py", script_path)

    # Step 2
    print("Note: first run downloads BAAI/bge-m3 (~2.2GB). This may take a few minutes.")
    script_path = str(ROOT / "scripts/shots/seed_all_shots.py")
    step2_ok = run_step(2, 4, "seed_all_shots.py", script_path)

    # Step 3
    if not skip_llm:
        script_path = str(ROOT / "scripts/shots/gen_ontology.py")
        step3_ok = run_step(3, 4, "gen_ontology.py", script_path)
    else:
        print("\n[STEP 3/4] gen_ontology.py - SKIPPED")

    # Step 4
    if not skip_llm:
        script_path = str(ROOT / "scripts/shots/gen_relevance_prompt.py")
        step4_ok = run_step(4, 4, "gen_relevance_prompt.py", script_path)
    else:
        print("\n[STEP 4/4] gen_relevance_prompt.py - SKIPPED")

    # Final checklist
    print("\n=== Bootstrap complete ===")
    print(f"[{'✓' if step1_ok else ' '}] derived_keywords.json generated")
    print(f"[{'✓' if step2_ok else ' '}] Qdrant profile_shots_bgem3 seeded (40 vectors)")
    print(f"[{'✓' if step3_ok else ' '}] derived_ontology.json generated")
    print(f"[{'✓' if step4_ok else ' '}] generated_relevance_prompt.txt generated")
    print(
        f"[{'✓' if step4_ok else ' '}] relevance prompt persisted to KV store (if store configured)"
    )

    print("\n=== Next steps ===")
    print("[ ] Set JOB_FTCH_OPENAI_API_KEY in .env.dev  (if not already set)")
    print("[ ] Set JOB_FTCH_TELEGRAM_BOT_TOKEN in job_ftch/adapters/telegram_bot/.env.dev")
    print("[ ] Set JOB_FTCH_TELEGRAM_API_ID + JOB_FTCH_TELEGRAM_API_HASH in .env.dev")
    print("[ ] Set JOB_FTCH_STORE_DSN (Postgres connection string) in .env.dev")
    print(
        "[ ] Copy fixtures/bootstrap/tenant_ai_jobs.yaml → job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml"
    )
    print("[ ] Run: uv run python job_ftch/adapters/telegram_bot/main.py")


if __name__ == "__main__":
    main()
