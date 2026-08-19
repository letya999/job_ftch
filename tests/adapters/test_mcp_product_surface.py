"""Unit tests for bot-parity MCP product helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_ftch.adapters.mcp.product_surface import (
    add_operator_example,
    clear_operator_examples,
    example_kind,
    filter_job_groups,
    get_examples_summary,
    list_operator_examples,
    public_job_group,
    remove_operator_example,
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


def test_public_job_group_exposes_title() -> None:
    job = SimpleNamespace(
        title="Senior LLM Engineer",
        title_normalized="senior llm engineer",
        title_raw="Senior LLM Engineer",
        company="Aston",
        company_canonical=None,
    )
    group = SimpleNamespace(
        canonical_job=job,
        model_dump=lambda mode="json": {
            "group_id": "abc",
            "canonical_job": {"title": "Senior LLM Engineer", "company": "Aston"},
        },
    )
    payload = public_job_group(group)
    assert payload["title"] == "Senior LLM Engineer"
    assert payload["company"] == "Aston"
    assert payload["group_id"] == "abc"


class _MemoryRunner:
    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str, str], object] = {}
        self._runtime = SimpleNamespace(
            llm_provider=None,
            ontology_store=None,
            embedding_provider=None,
        )

    def get_runtime(self, tenant_id: str) -> SimpleNamespace:
        del tenant_id
        return self._runtime

    async def list_candidate_profiles(
        self, tenant_id: str, user_id: str
    ) -> list[dict[str, object]]:
        del tenant_id
        out: list[dict[str, object]] = []
        for (_tid, uid, pid), managed in self._profiles.items():
            if uid != user_id:
                continue
            out.append({"profile_id": pid, "user_id": uid, "active": True, "managed": managed})
        return out

    async def get_candidate_profile(
        self, tenant_id: str, user_id: str, profile_id: str
    ) -> object | None:
        return self._profiles.get((tenant_id, user_id, profile_id))

    async def save_and_activate_candidate_profile(self, tenant_id: str, managed: object) -> None:
        self._profiles[(tenant_id, managed.user_id, managed.profile_id)] = managed  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_operator_examples_map_resume_and_vacancy() -> None:
    runner = _MemoryRunner()
    cases = (
        ("resume", "positive", "Senior ML engineer resume"),
        ("resume", "negative", "Accountant resume"),
        ("vacancy", "positive", "Hiring LLM engineer"),
        ("vacancy", "negative", "Hiring salesperson"),
    )
    for kind, label, text in cases:
        result = await add_operator_example(
            runner,
            tenant_id="t1",
            user_id="u1",
            kind=kind,
            label=label,
            text=text,
            refresh_policy="defer",
        )
        assert "error" not in result
        assert result["kind"] == kind
        assert result["label"] == label
        assert result["prefilter_dirty"] is True
        assert result["refresh_deferred"] is True

    listed = await list_operator_examples(runner, tenant_id="t1", user_id="u1")
    assert listed["counts"] == {
        "positive_resume": 1,
        "negative_resume": 1,
        "positive_vacancy": 1,
        "negative_vacancy": 1,
    }
    assert "positive_job" not in listed["examples"]
    assert listed["examples"]["positive_vacancy"] == ["Hiring LLM engineer"]

    vacancies = await list_operator_examples(
        runner, tenant_id="t1", user_id="u1", kind="vacancy", label="negative"
    )
    assert set(vacancies["examples"]) == {"negative_vacancy"}
    assert vacancies["counts"]["negative_vacancy"] == 1

    summary = await get_examples_summary(runner, tenant_id="t1", user_id="u1")
    assert summary["total"] == 4
    assert summary["counts"]["positive_resume"] == 1

    removed = await remove_operator_example(
        runner,
        tenant_id="t1",
        user_id="u1",
        kind="vacancy",
        label="negative",
        index=0,
    )
    assert removed["counts"]["negative_vacancy"] == 0
    assert removed["prefilter_dirty"] is True

    cleared = await clear_operator_examples(runner, tenant_id="t1", user_id="u1", kind="resume")
    assert cleared["removed"] == 2
    assert cleared["counts"]["positive_resume"] == 0
    assert cleared["counts"]["negative_resume"] == 0
    assert cleared["counts"]["positive_vacancy"] == 1


@pytest.mark.asyncio
async def test_operator_examples_reject_invalid_kind() -> None:
    runner = _MemoryRunner()
    result = await add_operator_example(
        runner,
        tenant_id="t1",
        user_id="u1",
        kind="job",
        label="positive",
        text="should not store",
    )
    assert result["error"] == "invalid_arguments"
    listed = await list_operator_examples(runner, tenant_id="t1", user_id="u1", kind="shots")
    assert listed["error"] == "invalid_arguments"
