from __future__ import annotations

from job_ftch.infrastructure.sources import source_policy


def test_resolve_source_policy_marks_freelance_boards_separately(monkeypatch) -> None:
    fake_hints = (
        (
            ("freelance.example",),
            source_policy.SourcePolicyHint(
                family="freelance_board",
                policy_name="freelance_board",
                allows_generic_job_pipeline=False,
                rationale="test",
            ),
        ),
    )
    monkeypatch.setattr(source_policy, "_POLICY_HINTS", fake_hints)
    hint = source_policy.resolve_source_policy("https://freelance.example/jobs")

    assert hint.family == "freelance_board"
    assert hint.allows_generic_job_pipeline is False


def test_resolve_source_policy_marks_service_marketplaces_separately(monkeypatch) -> None:
    fake_hints = (
        (
            ("service.example",),
            source_policy.SourcePolicyHint(
                family="service_marketplace",
                policy_name="service_marketplace",
                allows_generic_job_pipeline=False,
                rationale="test",
            ),
        ),
    )
    monkeypatch.setattr(source_policy, "_POLICY_HINTS", fake_hints)
    hint = source_policy.resolve_source_policy("https://service.example/")

    assert hint.family == "service_marketplace"
    assert hint.allows_generic_job_pipeline is False


def test_source_policy_metadata_defaults_to_career_site() -> None:
    metadata = source_policy.source_policy_metadata("https://example.com/careers")

    assert metadata["source_policy"] == "career_site"
    assert metadata["source_family"] == "career_site"
