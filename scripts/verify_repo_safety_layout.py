"""Verify the repository-local safety tooling layout.

The repository intentionally keeps most safety tool configs under
``.repo-safety/`` rather than scattering them through the root.
This script validates both the file layout and, optionally, the
commands that consume those relocated configs.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_PRESENT = (
    ".repo-safety/pre-commit-config.yaml",
    ".repo-safety/gitleaks.toml",
    ".repo-safety/detect-secrets.baseline",
    ".repo-safety/config.json",
    ".repo-safety/opengrep",
    ".github/renovate.json",
)

EXPECTED_ABSENT = (
    ".pre-commit-config.yaml",
    ".gitleaks.toml",
    ".secrets.baseline",
    ".semgrepignore",
    "bandit.yaml",
    "renovate.json",
)


def _ensure_paths() -> None:
    missing = [rel for rel in EXPECTED_PRESENT if not (ROOT / rel).exists()]
    unexpected = [rel for rel in EXPECTED_ABSENT if (ROOT / rel).exists()]
    if missing:
        raise SystemExit(f"missing relocated safety assets: {', '.join(missing)}")
    if unexpected:
        raise SystemExit(f"unexpected root-level safety assets: {', '.join(unexpected)}")


def _ensure_workflow_references() -> None:
    security = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
    if ".repo-safety/gitleaks.toml" not in security:
        raise SystemExit("security workflow does not reference .repo-safety/gitleaks.toml")

    sast = (ROOT / ".github" / "workflows" / "sast.yml").read_text(encoding="utf-8")
    if "--exclude .repo-safety/opengrep" not in sast:
        raise SystemExit("sast workflow does not exclude .repo-safety/opengrep")


def _run(command: tuple[str, ...]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def _run_smoke_commands() -> None:
    _run(("uv", "run", "pre-commit", "validate-config", ".repo-safety/pre-commit-config.yaml"))
    _run(
        (
            "uv",
            "run",
            "pre-commit",
            "run",
            "-c",
            ".repo-safety/pre-commit-config.yaml",
            "--all-files",
        )
    )
    _run(
        (
            "opengrep",
            "scan",
            "--config",
            ".repo-safety/opengrep",
            "--exclude",
            ".repo-safety/opengrep",
            ".",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-commands",
        action="store_true",
        help="also run the relocated pre-commit and opengrep smoke commands",
    )
    args = parser.parse_args()

    _ensure_paths()
    _ensure_workflow_references()
    if args.check_commands:
        _run_smoke_commands()

    print("repo safety layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
