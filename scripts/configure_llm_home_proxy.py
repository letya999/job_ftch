"""Configure CLIProxy home egress without printing or copying credentials into logs."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml


def configure(path: Path, backup_dir: Path) -> Path:
    path = path.resolve(strict=True)
    original = path.read_bytes()
    config = yaml.safe_load(original)
    if not isinstance(config, dict):
        raise ValueError("CLIProxy config must be a YAML mapping")
    if config.get("config-version") == 8:
        config.setdefault("requests", {})["proxy-url"] = "http://llm-home-egress:1055"
        retry = config.setdefault("routing", {}).setdefault("retry", {})
    else:
        config["proxy-url"] = "http://llm-home-egress:1055"
        retry = config
    retry["request-retry"] = 0
    retry["max-retry-credentials"] = 1
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    backup = backup_dir / f"cliproxy-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.yaml"
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(original)
    fd, temporary = tempfile.mkstemp(prefix=".cliproxy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=Path(".runtime/backups/llm-home"))
    args = parser.parse_args()
    backup = configure(args.config, args.backup_dir)
    print(f"Home proxy configured; protected backup: {backup}")
    print("Recreate CLIProxy to reopen its read-only config mount.")


if __name__ == "__main__":
    main()
