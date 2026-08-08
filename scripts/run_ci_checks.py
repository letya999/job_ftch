"""Canonical local CI command groups.

Run with ``uv run python scripts/run_ci_checks.py <group>``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "lint": (
        ("ruff", "check", "."),
        (sys.executable, "scripts/check_import_hygiene.py"),
        (sys.executable, "scripts/check_module_boundaries.py"),
        (sys.executable, "scripts/lint_docs.py"),
        (
            sys.executable,
            "scripts/validate_yaml_schemas.py",
            "config/runtime.prod.yaml",
            "config/pipelines/evidence_v2_compact_prefilter.yaml",
            "job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml",
        ),
        (sys.executable, "scripts/check_config_layers.py"),
        ("ruff", "format", "--check", "."),
    ),
    "type": (("mypy", "job_ftch"),),
    "architecture": (
        (sys.executable, "scripts/check_import_hygiene.py"),
        (sys.executable, "scripts/check_module_boundaries.py"),
        (sys.executable, "scripts/check_config_layers.py"),
    ),
    "test-smoke": (
        (
            "pytest",
            "tests/test_smoke.py",
            "tests/test_suite_guardrails.py",
            "tests/domain",
            "tests/unit",
            "job_ftch/adapters/telegram_bot/tests",
            "-q",
            "-o",
            "addopts=",
            "--tb=short",
        ),
    ),
    "test": (
        (sys.executable, "scripts/check_test_layout.py"),
        (
            "pytest",
            "tests",
            "job_ftch/adapters/telegram_bot/tests",
            "-m",
            "not network",
            "--cov",
            "--cov-fail-under=70",
            "--cov-report=xml",
        ),
    ),
    "security": (
        ("bandit", "-r", "job_ftch", "scripts/check_module_boundaries.py", "-ll"),
        ("pip-audit", "--ignore-vuln", "GHSA-w596-868m-8v6m"),
    ),
    "repo-safety": ((sys.executable, "scripts/verify_repo_safety_layout.py"),),
    "core-import": (
        (
            sys.executable,
            "-c",
            "import job_ftch; import job_ftch.application.contracts; import job_ftch.cli",
        ),
    ),
    "release-contract": (
        (
            "pytest",
            "tests/application/test_tenant_graph_runtime.py",
            "tests/test_pipeline_eval_reporting.py",
            "tests/eval/test_champion_recipe.py",
            "tests/eval/test_production_recipe.py",
            "tests/infrastructure/sources/test_ingest_outcome_contract.py",
            "-q",
        ),
        (sys.executable, "scripts/evaluate_classification.py", "--gate"),
        (sys.executable, "scripts/evaluate_extraction.py", "--gate"),
    ),
    "eval-filtering": (
        (sys.executable, "scripts/evaluate_classification.py", "--gate"),
        (
            "pytest",
            "tests/test_classification_evaluation.py",
            "tests/nodes/test_tfidf_logreg_prefilter.py",
            "tests/eval/test_train_relevance_prefilter.py",
            "-q",
        ),
    ),
    "eval-publishing": (
        ("pytest", "tests/publication", "-q"),
        (
            sys.executable,
            "scripts/publication/run_card_eval.py",
            "--gate",
            "--allow-missing-fixtures",
        ),
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=(*COMMANDS, "all"))
    args = parser.parse_args()
    groups = tuple(COMMANDS) if args.group == "all" else (args.group,)
    for group in groups:
        for command in COMMANDS[group]:
            subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
