"""Unit tests for bot-parity MCP product helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.adapters.mcp.product_surface import (
    example_kind,
    filter_job_groups,
    resolve_surface,
    shot_role,
)


def test_resolve_surface_defaults_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOB_FTCH_MCP_SURFACE", raising=False)
    assert resolve_surface() == "core"
    monkeypatch.setenv("JOB_FTCH_MCP_SURFACE", "admin")
    assert resolve_surface() == "admin"
    monkeypatch.setenv("JOB_FTCH_MCP_SURFACE", "nope")
    assert resolve_surface() == "core"


def test_example_kind_and_role() -> None:
    assert example_kind("positive", "resume") == "positive_resume"
    assert example_kind("negative", "job") == "negative_job"
    assert "vacancy:positive" in shot_role(
        user_id="u", tenant_id="t", kind="job", polarity="positive"
    )


def test_filter_job_groups_by_company_and_score() -> None:
    def _group(company: str, score: float | None, location: str = "Remote") -> SimpleNamespace:
        job = SimpleNamespace(
            company=company,
            company_canonical=company,
            location=location,
            work_mode=SimpleNamespace(value="remote"),
            language=SimpleNamespace(value="en"),
            best_score=score,
            routing_decision=None,
            source_name="habr",
        )
        return SimpleNamespace(
            canonical_job=job,
            jobs=[job],
            model_dump=lambda mode="json": {
                "canonical_job": {
                    "company": company,
                    "location": location,
                    "best_score": score,
                }
            },
        )

    groups = [
        _group("OpenAI", 0.9, "SF"),
        _group("Yandex", 0.4, "Moscow"),
        _group("OpenAI Labs", 0.8, "Remote"),
    ]
    out = filter_job_groups(groups, limit=10, company="openai", min_score=0.85)
    assert len(out) == 1
    assert out[0]["canonical_job"]["company"] == "OpenAI"

    out2 = filter_job_groups(groups, limit=10, location="moscow")
    assert len(out2) == 1
    assert out2[0]["canonical_job"]["company"] == "Yandex"
