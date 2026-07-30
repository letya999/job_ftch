"""File-based cache utilities with TTL and corruption detection.

Patterns ported from Botasaurus (omkarcloud/botasaurus, cache.py):
- `is_file_newer_than()` — check if a file's mtime exceeds a TTL
- `safe_read_json()` — read JSON with auto-cleanup on corruption/missing

These are standalone utilities for any file-based caching (LLM responses,
HTTP response caches, etc.). They do NOT depend on the Store protocol.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Any


class CacheMiss(Exception):
    """Raised when a cached item is not found or is corrupted."""


def is_file_newer_than(
    path: str | Path,
    *,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0,
) -> bool:
    """Return True if the file at *path* is newer than the given TTL.

    Uses the file's modification time (os.path.getmtime).

    Returns True if the file does not exist (stale = not cacheable).

    Examples::

        if is_file_newer_than("cache/resp.json", hours=6):
            # cache expired, re-fetch
    """
    p = Path(path)
    if not p.exists():
        return True
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
    except (OSError, ValueError):
        return True
    age = datetime.now(UTC) - mtime
    ttl = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return age > ttl


def safe_read_json(path: str | Path) -> Any:
    """Read a JSON file, returning None on corruption or missing file.

    On corruption (JSONDecodeError, FileNotFoundError), the file is
    deleted to prevent repeated failures (Botasaurus safe_get pattern).

    Returns None if the file doesn't exist or is corrupted.
    Raises CacheMiss for explicit callers who want an exception.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            # Empty file — treat as corruption
            p.unlink(missing_ok=True)
            return None
        return json.loads(text)
    except (JSONDecodeError, OSError):
        # Corrupted or unreadable — delete and return None
        p.unlink(missing_ok=True)
        return None


def read_json_strict(path: str | Path) -> Any:
    """Read a JSON file, raising CacheMiss on any error.

    Like safe_read_json but raises instead of returning None.
    Deletes corrupted files.
    """
    result = safe_read_json(path)
    if result is None:
        raise CacheMiss(str(path))
    return result


def write_json(data: Any, path: str | Path) -> None:
    """Atomically write data to a JSON file, creating parent dirs as needed.

    Writes to a temp file in the same directory then ``os.replace`` onto the
    target, so a crash mid-write cannot leave a partially written (corrupt)
    cache file — which is precisely what ``safe_read_json`` guards against.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, default=str)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def cache_path_for(
    namespace: str,
    key: str,
    *,
    base_dir: str | Path = "cache",
) -> Path:
    """Generate a deterministic cache file path from a namespace and key.

    Uses a simple hash of the key for the filename.

    Example::

        p = cache_path_for("llm_responses", "https://example.com/api")
        # → cache/llm_responses/a1b2c3d4.json
    """
    # md5 is used only to derive a stable cache filename, never for security.
    h = hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()
    return Path(base_dir) / namespace / f"{h}.json"
