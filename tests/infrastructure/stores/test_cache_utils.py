"""Tests for file-based cache utilities."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from job_ftch.infrastructure.stores.cache_utils import (
    CacheMiss,
    cache_path_for,
    is_file_newer_than,
    read_json_strict,
    safe_read_json,
    write_json,
)


class TestIsFileOlderThan:
    def test_missing_file_is_stale(self, tmp_path: Path) -> None:
        assert is_file_newer_than(tmp_path / "nope.json", hours=1) is True

    def test_fresh_file_not_stale(self, tmp_path: Path) -> None:
        p = tmp_path / "fresh.json"
        p.write_text("{}", encoding="utf-8")
        assert is_file_newer_than(p, hours=1) is False

    def test_new_file_is_stale(self, tmp_path: Path) -> None:
        p = tmp_path / "new.json"
        p.write_text("{}", encoding="utf-8")
        # Backdate mtime by 2 hours
        new = time.time() - 7200
        os.utime(p, (new, new))
        assert is_file_newer_than(p, hours=1) is True


class TestSafeReadJson:
    def test_reads_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        write_json({"a": 1}, p)
        assert safe_read_json(p) == {"a": 1}

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert safe_read_json(tmp_path / "missing.json") is None

    def test_corrupt_is_deleted_and_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert safe_read_json(p) is None
        assert not p.exists()  # auto-cleaned

    def test_empty_is_deleted_and_none(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("   ", encoding="utf-8")
        assert safe_read_json(p) is None
        assert not p.exists()


class TestReadJsonStrict:
    def test_raises_on_missing(self, tmp_path: Path) -> None:
        with pytest.raises(CacheMiss):
            read_json_strict(tmp_path / "missing.json")

    def test_returns_value(self, tmp_path: Path) -> None:
        p = tmp_path / "ok.json"
        write_json([1, 2, 3], p)
        assert read_json_strict(p) == [1, 2, 3]


class TestWriteJson:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "deep" / "x.json"
        write_json({"k": "v"}, p)
        assert json.loads(p.read_text(encoding="utf-8")) == {"k": "v"}

    def test_atomic_no_tmp_left_behind(self, tmp_path: Path) -> None:
        p = tmp_path / "x.json"
        write_json({"k": "v"}, p)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_overwrite_is_clean(self, tmp_path: Path) -> None:
        p = tmp_path / "x.json"
        write_json({"v": 1}, p)
        write_json({"v": 2}, p)
        assert safe_read_json(p) == {"v": 2}


class TestCachePathFor:
    def test_deterministic(self) -> None:
        a = cache_path_for("ns", "key")
        b = cache_path_for("ns", "key")
        assert a == b

    def test_namespaced(self) -> None:
        p = cache_path_for("llm", "https://example.com", base_dir="cache")
        assert p.parent.name == "llm"
        assert p.suffix == ".json"

    def test_roundtrip_through_path(self, tmp_path: Path) -> None:
        p = cache_path_for("ns", "some-key", base_dir=tmp_path)
        write_json({"cached": True}, p)
        assert safe_read_json(p) == {"cached": True}
