from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path("scripts/eval/merge_pool.py")
    spec = importlib.util.spec_from_file_location("merge_pool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dirty_normalization_preserves_capture_times_for_temporal_holdout() -> None:
    module = _module()
    row = {
        "stable_id": "new-item",
        "source_kind": "telegram_channel",
        "source_name": "jobs",
        "text": "Hiring an applied AI engineer for production LLM products.",
        "fetched_at": "2026-07-15T12:00:00Z",
        "created_at": "2026-07-15T11:30:00Z",
    }

    normalized = module._normalize_dirty_row(row)

    assert normalized is not None
    assert normalized["fetched_at"] == "2026-07-15T12:00:00Z"
    assert normalized["created_at"] == "2026-07-15T11:30:00Z"
    assert normalized["relevant"] == -1


def test_clean_normalization_keeps_existing_capture_time_without_inventing_one() -> None:
    module = _module()
    normalized = module._normalize_clean_row(
        {
            "stable_id": "new-item",
            "text": "A sufficiently long vacancy text for a relevant evaluation row.",
            "relevant": 1,
            "fetched_at": "2026-07-14T12:00:00Z",
        }
    )

    assert normalized is not None
    assert normalized["fetched_at"] == "2026-07-14T12:00:00Z"
    assert "created_at" not in normalized


def test_merge_pool_accepts_dirty_only_dataset_mode(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(sys, "argv", ["merge_pool.py", "--dirty-only"])

    assert module.parse_args().dirty_only is True
