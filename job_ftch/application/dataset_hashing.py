"""Platform-independent hashing for versioned JSONL evaluation datasets."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def canonical_jsonl_bytes(path: Path) -> bytes:
    """Normalize newline spelling and one terminal newline before hashing."""
    lines = path.read_text(encoding="utf-8").splitlines()
    canonical = "\n".join(lines)
    if lines:
        canonical += "\n"
    return canonical.encode("utf-8")


def dataset_hash(path: Path) -> str:
    """Return a stable SHA-256 on Windows and POSIX checkouts."""
    return hashlib.sha256(canonical_jsonl_bytes(path)).hexdigest()
