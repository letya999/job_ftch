"""Keyword-list loader for the heuristic post-type classifier.

Owns:

- Loading the four categories (announcement, job_posting, candidate,
  spam) from the `keyword_lists.yaml` file co-located with this module once per process.
- mtime-based cache: the file is re-read only when its modification
  time changes. This makes per-item classification cheap in steady state.

The lists are exposed as `load_announcement_tokens()`,
`load_job_posting_tokens()`, `load_candidate_tokens()`,
`load_spam_tokens()`. All four return tuples of lower-cased substrings.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping


def _resolve_default_path() -> str:
    # keyword_lists.yaml lives next to this module file.
    return os.path.join(os.path.dirname(__file__), "keyword_lists.yaml")


_CACHE: dict[str, Any] = {"mtime": 0.0, "data": {}}
_LOCK = threading.Lock()


def _load(path: str) -> Mapping[str, tuple[str, ...]]:
    """Load the YAML, return a dict[str, tuple[str, ...]] (lower-cased)."""
    with _LOCK:
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            return {}
        if _CACHE.get("path") == path and _CACHE.get("mtime") == mtime:
            return _CACHE["data"]  # type: ignore
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, dict):
            return {}
        data: dict[str, tuple[str, ...]] = {}
        for category, tokens in raw.items():
            if isinstance(tokens, list):
                data[str(category)] = tuple(str(t).lower() for t in tokens)
        _CACHE["path"] = path
        _CACHE["mtime"] = mtime
        _CACHE["data"] = data
        return data


def _get(category: str, path: str | None = None) -> tuple[str, ...]:
    if path is None:
        path = _resolve_default_path()
    return _load(path).get(category, ())


def load_keyword_lists(path: str | None = None) -> Mapping[str, tuple[str, ...]]:
    if path is None:
        path = _resolve_default_path()
    return _load(path)


def load_announcement_tokens(path: str | None = None) -> tuple[str, ...]:
    return _get("announcement", path)


def load_job_posting_tokens(path: str | None = None) -> tuple[str, ...]:
    return _get("job_posting", path)


def load_job_posting_strong_tokens(path: str | None = None) -> tuple[str, ...]:
    return _get("job_posting_strong", path)


def load_candidate_tokens(path: str | None = None) -> tuple[str, ...]:
    return _get("candidate", path)


def load_spam_tokens(path: str | None = None) -> tuple[str, ...]:
    return _get("spam", path)


def reset_cache() -> None:
    """Drop the in-memory cache. Used by tests that mutate the YAML."""
    with _LOCK:
        _CACHE["mtime"] = 0.0
        _CACHE["data"] = {}
        _CACHE.pop("path", None)
