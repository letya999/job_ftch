from __future__ import annotations

from job_ftch.infrastructure.embeddings.bgem3 import _resolve_fp16


def test_bgem3_auto_mode_is_explicitly_overridable() -> None:
    assert _resolve_fp16(False) is False
    assert _resolve_fp16(True) is True
