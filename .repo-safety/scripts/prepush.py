from __future__ import annotations

import shutil
import subprocess  # nosec
import sys


def run(command: list[str], required: bool = False) -> int:
    if not shutil.which(command[0]):
        print(f"[repo-safety] {'ERROR' if required else 'WARN'}: missing {command[0]}")
        return 2 if required else 0
    print(f"[repo-safety] running: {' '.join(command)}")
    proc = subprocess.run(command)  # nosec
    return proc.returncode


def git_has_commits() -> bool:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True)  # nosec
    return proc.returncode == 0


def trufflehog_since_commit() -> str | None:
    """Pick a safe 'since' revision for TruffleHog. Returns None
    if no stable lower bound is available, in which case the
    caller should fall back to a filesystem scan rather than
    passing an invalid SHA like HEAD~20 on a short history."""
    candidates: list[list[str]] = [
        ["git", "merge-base", "HEAD", "@{upstream}"],
        ["git", "merge-base", "HEAD", "origin/main"],
        ["git", "merge-base", "HEAD", "origin/dev"],
    ]
    for cmd in candidates:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)  # nosec
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if sha:
                return sha
    proc = subprocess.run(
        ["git", "rev-list", "--max-count=20", "HEAD"],
        capture_output=True,
        text=True,
        timeout=20,  # nosec
    )
    if proc.returncode == 0:
        commits = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if len(commits) >= 2:
            return commits[-1]
    return None


def python_executable() -> str:
    """Prefer sys.executable over the bare `python` alias so the
    local helper scripts work even when the host has no `python`
    shim on PATH (common on minimal Linux images and on some
    Windows installs)."""
    return sys.executable


def main() -> int:
    py = python_executable()
    failures = 0
    checks: list[tuple[list[str], bool]] = [
        ([py, ".repo-safety/scripts/forbid_sensitive_files.py", "--all"], True),
        ([py, ".repo-safety/scripts/scan_mcp_config.py"], True),
        (["gitleaks", "git", ".", "--redact", "--exit-code", "1"], False),
    ]
    if git_has_commits():
        since = trufflehog_since_commit()
        if since:
            checks.append(
                (
                    [
                        "trufflehog",
                        "git",
                        "file://.",
                        "--exclude-paths",
                        ".repo-safety/trufflehog-exclude.txt",
                        "--since-commit",
                        since,
                        "--results=verified,unknown",
                        "--fail",
                    ],
                    False,
                )
            )
        else:
            checks.append(
                (
                    [
                        "trufflehog",
                        "filesystem",
                        ".",
                        "--exclude-paths",
                        ".repo-safety/trufflehog-exclude.txt",
                        "--results=verified,unknown",
                        "--fail",
                    ],
                    False,
                )
            )
    else:
        checks.append(
            (
                [
                    "trufflehog",
                    "filesystem",
                    ".",
                    "--exclude-paths",
                    ".repo-safety/trufflehog-exclude.txt",
                    "--results=verified,unknown",
                    "--fail",
                ],
                False,
            )
        )

    for command, required in checks:
        code = run(command, required=required)
        if code not in (0, 2):
            failures += 1
        if code == 2 and required:
            failures += 1
    if failures:
        print("[repo-safety] push blocked")
        return 1
    print("[repo-safety] pre-push passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
