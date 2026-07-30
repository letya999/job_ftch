from __future__ import annotations

import argparse
import fnmatch
import subprocess  # nosec
from pathlib import Path

DENY = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "credentials.*.json",
    "service-account*.json",
    "token.json",
    "tokens.json",
    "secrets.json",
    ".mcp.json",
    "claude_desktop_config.json",
    "*.ovpn",
]
ALLOW = [
    ".env.example",
    ".env.*.example",
    "example.credentials.json",
    "credentials.example.json",
]


def git_files(all_files: bool) -> list[str]:
    cmd = ["git", "ls-files"] if all_files else ["git", "diff", "--cached", "--name-only"]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)  # nosec
    except Exception:
        return []
    return [p.strip().replace("\\", "/") for p in out.splitlines() if p.strip()]


def matches(path: str, patterns: list[str]) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in patterns)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="scan all tracked files instead of staged files"
    )
    args = parser.parse_args()
    blocked = []
    for path in git_files(args.all):
        if matches(path, ALLOW):
            continue
        if matches(path, DENY):
            blocked.append(path)
    if blocked:
        print("[repo-safety] blocked sensitive files:")
        for path in blocked:
            print(f"  - {path}")
        print("Move real secrets out of Git. Keep only examples like .env.example.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
